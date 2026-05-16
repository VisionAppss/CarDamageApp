"""
backend/main.py
FastAPI бэкенд с подключением к PostgreSQL.
Хранит профили пользователей, историю анализов и события аналитики.
"""

import base64
import json
import os
import uuid
import configparser
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Literal

import httpx
import openai
import asyncpg
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
import yookassa
from yookassa import Configuration as YKConfig, Payment as YKPayment


# === КОНФИГУРАЦИЯ ===

config = configparser.ConfigParser()
config.read('config.ini')

if not config.sections():
    raise FileNotFoundError(
        "config.ini не найден! Скопируйте config.example.ini в config.ini и заполните ключи."
    )

YANDEX_API_KEY = config.get('yandex', 'api_key')
YANDEX_FOLDER  = config.get('yandex', 'folder_id')
YANDEX_MODEL   = config.get('yandex', 'model', fallback='yandexgpt-32k')

# Supabase JWT secret для верификации токенов
SUPABASE_JWT_SECRET = config.get('supabase', 'jwt_secret', fallback='')

UPLOAD_DIR = Path(os.getenv('UPLOAD_DIR', '/app/uploads'))
UPLOAD_DIR.mkdir(exist_ok=True)

ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', '')

# YooKassa
YOOKASSA_SHOP_ID   = config.get('yookassa', 'shop_id',    fallback='') or os.getenv('YOOKASSA_SHOP_ID', '')
YOOKASSA_SECRET    = config.get('yookassa', 'secret_key', fallback='') or os.getenv('YOOKASSA_SECRET_KEY', '')
YOOKASSA_PRICE     = config.get('yookassa', 'price',      fallback='299.00')
YOOKASSA_RETURN_URL = config.get('yookassa', 'return_url', fallback='https://car-scan.ru/')

if YOOKASSA_SHOP_ID and YOOKASSA_SECRET:
    YKConfig.account_id = YOOKASSA_SHOP_ID
    YKConfig.secret_key  = YOOKASSA_SECRET

# PostgreSQL
DB_HOST = os.getenv('DB_HOST', 'db')
DB_PORT = int(os.getenv('DB_PORT', 5432))
DB_NAME = os.getenv('DB_NAME', 'avtootsenka')
DB_USER = os.getenv('DB_USER', 'appuser')
DB_PASS = os.getenv('DB_PASSWORD', '')


# === МОДЕЛИ ДАННЫХ ===

class VehicleInfo(BaseModel):
    make: Optional[str] = None
    model: Optional[str] = None
    year: Optional[int] = None
    confidence: int = Field(ge=0, le=100, default=0)

    @field_validator('year')
    @classmethod
    def validate_year(cls, v):
        if v is not None and (v < 1950 or v > datetime.now().year + 1):
            return None
        return v


class CostRange(BaseModel):
    min: int = Field(ge=0)
    max: int = Field(ge=0)

    @field_validator('max')
    @classmethod
    def max_gte_min(cls, v, info):
        if info.data.get('min') is not None and v < info.data['min']:
            return info.data['min']
        return v


class DamageItem(BaseModel):
    type: Literal["scratch", "dent", "crack", "deformation", "corrosion", "other"]
    location: str
    severity: Literal["cosmetic", "minor", "moderate", "severe", "critical"]
    size_cm2: Optional[float] = Field(None, ge=0)
    description: str
    repair_method: str
    estimated_cost_rub: CostRange


class Summary(BaseModel):
    total_estimated_cost: CostRange
    repair_time_days: str
    drivable: Optional[bool] = None
    recommendations: List[str] = []
    requires_in_person_inspection: bool = True


class MetaInfo(BaseModel):
    photo_quality: Literal["good", "fair", "poor"]
    missing_angles: List[str] = []
    analysis_confidence: int = Field(ge=0, le=100, default=0)


class AnalysisResult(BaseModel):
    vehicle: VehicleInfo
    damages: List[DamageItem]
    summary: Summary
    meta: MetaInfo


class ProfileUpsert(BaseModel):
    user_id: str
    email: str


class EventLog(BaseModel):
    event: str
    params: dict = {}
    user_id: Optional[str] = None
    url: Optional[str] = None
    ua: Optional[str] = None

class FeedbackData(BaseModel):
    type: str          # bug / idea / access / other
    text: str
    email: Optional[str] = None
    user_id: Optional[str] = None
    url: Optional[str] = None

class PaymentCreateRequest(BaseModel):
    user_id: str

# === ПРОМПТЫ ===

SYSTEM_PROMPT = """You are a certified automotive damage assessor with 15 years of experience. Analyze the vehicle photo and return a damage assessment in strict JSON format.

DAMAGE TYPES (use exactly these values):
- scratch: surface scratch, paint level only
- dent: dent without paint damage
- crack: crack, chip, or tear in metal/plastic
- deformation: structural deformation
- corrosion: rust or corrosion
- other: other damage

SEVERITY LEVELS (use exactly these values):
- cosmetic: appearance only
- minor: minor damage, local repair
- moderate: significant repair needed
- severe: part replacement likely needed
- critical: safety risk, vehicle not drivable

REPAIR METHODS (use exactly these values in Russian):
- полировка
- PDR
- покраска детали
- покраска+рихтовка
- замена детали

COST ESTIMATES: provide in Russian Rubles (RUB). Typical ranges:
- scratch/polish: 2000-8000 RUB
- dent/PDR: 3000-15000 RUB
- panel paint: 8000-25000 RUB
- panel replace: 15000-60000 RUB
- windshield: 10000-40000 RUB

Return ONLY valid JSON, no markdown, no explanation:
{
  "vehicle": {
    "make": "string or null",
    "model": "string or null",
    "year": number or null,
    "confidence": number 0-100
  },
  "damages": [
    {
      "type": "scratch|dent|crack|deformation|corrosion|other",
      "location": "specific part name in Russian",
      "severity": "cosmetic|minor|moderate|severe|critical",
      "size_cm2": number or null,
      "description": "description in Russian",
      "repair_method": "полировка|PDR|покраска детали|покраска+рихтовка|замена детали",
      "estimated_cost_rub": {"min": number, "max": number}
    }
  ],
  "summary": {
    "total_estimated_cost": {"min": number, "max": number},
    "repair_time_days": "e.g. 3-5 дней",
    "drivable": true or false or null,
    "recommendations": ["recommendation in Russian"],
    "requires_in_person_inspection": true or false
  },
  "meta": {
    "photo_quality": "good|fair|poor",
    "missing_angles": ["missing angle in Russian"],
    "analysis_confidence": number 0-100
  }
}"""


def get_user_prompt(inspection_type: str = "страховой случай") -> str:
    return f"""Analyze this vehicle photo for damage assessment.

Context:
- Date: {datetime.now().strftime("%Y-%m-%d")}
- Inspection type: {inspection_type}

Instructions:
- Identify ALL visible damage
- For each damage provide estimated repair cost in RUB
- If unsure about something, set low confidence and recommend in-person inspection
- Describe damage location and repair method in Russian

Return ONLY the JSON object, no markdown, no ```json wrapper."""


# === YANDEX GPT ===

http_client = httpx.Client(timeout=60.0)

ai_client = openai.OpenAI(
    api_key=YANDEX_API_KEY,
    base_url="https://llm.api.cloud.yandex.net/v1",
    http_client=http_client,
)


# === FASTAPI ===

app = FastAPI(
    title="Car Damage Analyzer Pro",
    description="API для анализа повреждений автомобилей через YandexGPT",
    version="3.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# === БД: пул соединений ===

@app.on_event("startup")
async def startup():
    app.state.pool = await asyncpg.create_pool(
        host=DB_HOST, port=DB_PORT,
        database=DB_NAME, user=DB_USER, password=DB_PASS,
        min_size=2, max_size=10
    )


@app.on_event("shutdown")
async def shutdown():
    await app.state.pool.close()


# === ХЕЛПЕРЫ ===

def clean_json(raw: str) -> str:
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 3:
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
    raw = raw.strip()
    s, e = raw.find('{'), raw.rfind('}')
    if s != -1 and e != -1 and e > s:
        raw = raw[s:e+1]
    return raw


def normalize_damage(d: dict) -> dict:
    """Нормализует повреждение из ответа Gemma в нашу схему."""
    # size_cm2: может прийти как area, size, size_cm2
    size = d.get('size_cm2') or d.get('area') or d.get('size') or None

    # estimated_cost_rub: может прийти как estimated_cost, cost, estimated_cost_rub
    cost = d.get('estimated_cost_rub') or d.get('estimated_cost') or d.get('cost')
    if not cost or not isinstance(cost, dict):
        cost = {"min": 3000, "max": 10000}  # fallback
    if 'min' not in cost: cost['min'] = 0
    if 'max' not in cost: cost['max'] = cost.get('min', 0)

    # repair_method: нормализуем на русский
    method_map = {
        'replacement': 'замена детали',
        'polish': 'полировка',
        'polishing': 'полировка',
        'pdr': 'PDR',
        'paint': 'покраска детали',
        'painting': 'покраска детали',
        'paint+straightening': 'покраска+рихтовка',
        'paints+richovka': 'покраска+рихтовка',
        'рихтовка': 'покраска+рихтовка',
    }
    method = d.get('repair_method', 'замена детали')
    method = method_map.get(method.lower(), method)

    return {
        'type': d.get('type', 'other'),
        'location': d.get('location', ''),
        'severity': d.get('severity', 'moderate'),
        'size_cm2': size,
        'description': d.get('description', ''),
        'repair_method': method,
        'estimated_cost_rub': cost,
    }


def normalize_result(data: dict) -> dict:
    """Нормализует полный ответ модели в нашу схему."""
    damages = [normalize_damage(d) for d in (data.get('damages') or [])]

    summary = data.get('summary') or {}
    total = summary.get('total_estimated_cost')
    if not total or not isinstance(total, dict):
        # Считаем из суммы повреждений
        total_min = sum(d['estimated_cost_rub']['min'] for d in damages)
        total_max = sum(d['estimated_cost_rub']['max'] for d in damages)
        total = {'min': total_min, 'max': total_max}

    meta = data.get('meta') or {}

    return {
        'vehicle': data.get('vehicle') or {},
        'damages': damages,
        'summary': {
            'total_estimated_cost': total,
            'repair_time_days': summary.get('repair_time_days', '—'),
            'drivable': summary.get('drivable'),
            'recommendations': summary.get('recommendations') or [],
            'requires_in_person_inspection': summary.get('requires_in_person_inspection', True),
        },
        'meta': {
            'photo_quality': meta.get('photo_quality', 'fair'),
            'missing_angles': meta.get('missing_angles') or [],
            'analysis_confidence': meta.get('analysis_confidence', 50),
        }
    }


# === ЭНДПОИНТЫ ===

@app.get("/health")
async def health():
    return {"status": "ok", "version": "3.0.0", "timestamp": datetime.now().isoformat()}


@app.post("/profile")
async def upsert_profile(data: ProfileUpsert):
    """Создать или обновить профиль пользователя после входа."""
    async with app.state.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO profiles (id, email)
            VALUES ($1, $2)
            ON CONFLICT (id) DO NOTHING
        """, data.user_id, data.email)
    return {"ok": True}


@app.post("/profile/{user_id}/consent")
async def mark_consent(user_id: str):
    """Отмечает что пользователь принял соглашение."""
    async with app.state.pool.acquire() as conn:
        await conn.execute("""
            UPDATE profiles SET consent_given = TRUE WHERE id = $1
        """, user_id)
    return {"ok": True}


@app.get("/profile/check-consent")
async def check_consent_by_email(email: str):
    """Проверяет давал ли пользователь согласие (по email, до входа)."""
    async with app.state.pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT consent_given FROM profiles WHERE email = $1
        """, email)
    return {"exists": row is not None, "consent_given": bool(row and row["consent_given"])}


@app.get("/profile/{user_id}")
async def get_profile(user_id: str):
    """Получить профиль пользователя."""
    async with app.state.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM profiles WHERE id = $1", user_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    return dict(row)


@app.get("/profile/{user_id}/analyses")
async def get_analyses(user_id: str, limit: int = 20):
    """История анализов пользователя."""
    async with app.state.pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, created_at, inspection_type, photo_name, result,
                   photo_data, photo_mime
            FROM analyses
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT $2
        """, user_id, limit)
    analyses = []
    for r in rows:
        row = dict(r)
        # asyncpg возвращает jsonb как строку — парсим обратно в dict
        if isinstance(row.get('result'), str):
            try:
                row['result'] = json.loads(row['result'])
            except Exception:
                pass
        analyses.append(row)
    return {"analyses": analyses}


@app.post("/event")
async def log_event(data: EventLog):
    """Записать аналитическое событие."""
    async with app.state.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO events (event, params, user_id, url, ua)
            VALUES ($1, $2, $3, $4, $5)
        """, data.event, json.dumps(data.params), data.user_id, data.url, data.ua)
    return {"ok": True}

@app.post("/feedback")
async def submit_feedback(data: FeedbackData):
    """Сохраняет обратную связь от пользователя."""
    async with app.state.pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id         BIGSERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                type       TEXT,
                text       TEXT,
                email      TEXT,
                user_id    TEXT,
                url        TEXT
            )
        """)
        await conn.execute("""
            INSERT INTO feedback (type, text, email, user_id, url)
            VALUES ($1, $2, $3, $4, $5)
        """, data.type, data.text, data.email, data.user_id, data.url)

    return {"ok": True}


@app.post("/analyze")
async def analyze_image(
    file: UploadFile = File(...),
    inspection_type: str = Form(default="страховой случай"),
    x_user_id: Optional[str] = Header(None),
):
    """Анализ фото. x-user-id — ID пользователя из Supabase Auth."""

    # Гостевой режим: без x_user_id — анализ делается, но не сохраняется в БД
    # Авторизованный: проверяем лимит и сохраняем результат
    if x_user_id:
        async with app.state.pool.acquire() as conn:
            profile = await conn.fetchrow(
                "SELECT analyses_count, is_paid, email FROM profiles WHERE id = $1",
                x_user_id
            )
            is_admin = ADMIN_EMAIL and profile and profile['email'] == ADMIN_EMAIL
            if not is_admin and profile and profile['analyses_count'] >= 1 and not profile['is_paid']:
                raise HTTPException(
                    status_code=402,
                    detail="Бесплатный анализ уже использован"
                )

    # Валидация файла
    allowed_types = ['image/jpeg', 'image/png', 'image/webp']
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Неподдерживаемый формат: {allowed_types}")

    contents = await file.read()

    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Файл слишком большой (макс 10 МБ)")
    if len(contents) < 10 * 1024:
        raise HTTPException(status_code=400, detail="Файл слишком маленький (мин 10 КБ)")

    # Сохраняем файл
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{file.filename}"
    file_path = UPLOAD_DIR / filename
    with open(file_path, "wb") as f:
        f.write(contents)

    base64_image = base64.b64encode(contents).decode('utf-8')

    # Запрос к YandexGPT
    try:
        response = ai_client.chat.completions.create(
            model=f"gpt://{YANDEX_FOLDER}/{YANDEX_MODEL}",
            temperature=0.1,
            max_tokens=2000,
            messages=[
                {"role": "system", "content": [{"type": "text", "text": SYSTEM_PROMPT}]},
                {"role": "user", "content": [
                    {"type": "text", "text": get_user_prompt(inspection_type)},
                    {"type": "image_url", "image_url": {"url": f"data:{file.content_type};base64,{base64_image}"}}
                ]}
            ]
        )

        raw = response.choices[0].message.content
        cleaned = clean_json(raw)

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            raise HTTPException(status_code=502, detail=f"Невалидный JSON от модели: {raw[:200]}")

        # Нормализуем ответ Gemma в нашу схему
        parsed = normalize_result(parsed)

        try:
            validated = AnalysisResult(**parsed)
            result_data = validated.model_dump()
        except Exception:
            result_data = parsed

        result = {
            "success": True,
            "data": result_data,
            "model_used": YANDEX_MODEL,
            "tokens_used": response.usage.total_tokens if response.usage else None,
            "processed_at": datetime.now().isoformat(),
            "filename": filename
        }

        # Сохраняем анализ и обновляем счётчик
        if x_user_id:
            async with app.state.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO analyses (user_id, result, photo_name, inspection_type, photo_data, photo_mime)
                    VALUES ($1, $2, $3, $4, $5, $6)
                """, x_user_id, json.dumps(result), file.filename, inspection_type,
                     base64_image, file.content_type)

                await conn.execute("""
                    UPDATE profiles
                    SET analyses_count = analyses_count + 1
                    WHERE id = $1
                """, x_user_id)

        return JSONResponse(result)

    except HTTPException:
        raise
    except Exception as e:
        if file_path.exists():
            file_path.unlink()
        raise HTTPException(status_code=502, detail=f"Ошибка нейросети: {str(e)}")


@app.post("/payment/create")
async def create_payment(data: PaymentCreateRequest):
    """Создаёт платёж YooKassa и возвращает URL для оплаты."""
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET:
        raise HTTPException(status_code=503, detail="Оплата временно недоступна")

    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchrow(
            "SELECT is_paid FROM profiles WHERE id = $1", data.user_id
        )
    if not profile:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    if profile['is_paid']:
        raise HTTPException(status_code=400, detail="Подписка уже активна")

    idempotency_key = str(uuid.uuid4())
    payment = YKPayment.create({
        "amount": {"value": YOOKASSA_PRICE, "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "return_url": YOOKASSA_RETURN_URL.rstrip('/') + "/?payment=success"
        },
        "capture": True,
        "description": "Безлимитный доступ к анализу повреждений автомобиля",
        "metadata": {"user_id": data.user_id}
    }, idempotency_key)

    async with app.state.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO payments (id, user_id, status, amount)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id) DO NOTHING
        """, payment.id, data.user_id, payment.status, float(YOOKASSA_PRICE))

    return {
        "payment_id": payment.id,
        "confirmation_url": payment.confirmation.confirmation_url
    }


@app.post("/payment/webhook")
async def payment_webhook(request: Request):
    """Вебхук от YooKassa — активирует подписку при успешной оплате."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = body.get("event")
    obj   = body.get("object", {})

    if event == "payment.succeeded":
        payment_id = obj.get("id")
        user_id    = obj.get("metadata", {}).get("user_id")
        if user_id:
            async with app.state.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE profiles SET is_paid = TRUE WHERE id = $1", user_id
                )
                if payment_id:
                    await conn.execute(
                        "UPDATE payments SET status = 'succeeded' WHERE id = $1", payment_id
                    )

    return {"ok": True}


@app.get("/payment/status/{payment_id}")
async def payment_status(payment_id: str):
    """Проверяет статус конкретного платежа через API YooKassa."""
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET:
        raise HTTPException(status_code=503, detail="Оплата временно недоступна")
    try:
        payment = YKPayment.find_one(payment_id)
        return {"status": payment.status, "paid": payment.paid}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
