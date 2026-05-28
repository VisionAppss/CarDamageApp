"""
backend/main.py
FastAPI бэкенд с подключением к PostgreSQL.
Хранит профили пользователей, историю анализов и события аналитики.
"""

import base64
import json
import os
import uuid
import secrets
import random
import configparser
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional, Literal

import httpx
import openai
import asyncpg
import aiosmtplib
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

# SMTP для OTP-авторизации
SMTP_HOST = config.get('smtp', 'host',     fallback='smtp.yandex.ru')
SMTP_PORT = int(config.get('smtp', 'port', fallback='465'))
SMTP_USER = config.get('smtp', 'user',     fallback='') or os.getenv('SMTP_USER', '')
SMTP_PASS = config.get('smtp', 'password', fallback='') or os.getenv('SMTP_PASSWORD', '')
SMTP_FROM = config.get('smtp', 'from',     fallback=SMTP_USER)

UPLOAD_DIR = Path(os.getenv('UPLOAD_DIR', '/app/uploads'))
UPLOAD_DIR.mkdir(exist_ok=True)

ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', '')

# YooKassa
YOOKASSA_SHOP_ID     = config.get('yookassa', 'shop_id',        fallback='') or os.getenv('YOOKASSA_SHOP_ID', '')
YOOKASSA_SECRET      = config.get('yookassa', 'secret_key',     fallback='') or os.getenv('YOOKASSA_SECRET_KEY', '')
YOOKASSA_ANALYSIS_PRICE = config.get('yookassa', 'analysis_price', fallback='149.00') or os.getenv('YOOKASSA_ANALYSIS_PRICE', '149.00')
YOOKASSA_PDF_PRICE   = config.get('yookassa', 'pdf_price',      fallback='99.00')  or os.getenv('YOOKASSA_PDF_PRICE', '99.00')
YOOKASSA_RETURN_URL  = config.get('yookassa', 'return_url',     fallback='https://car-scan.ru/')

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
    user_id: Optional[str] = None
    type: str = "pdf"
    customer_email: Optional[str] = None

class SendCodeRequest(BaseModel):
    email: str

class VerifyCodeRequest(BaseModel):
    email: str
    code: str

# === ПРОМПТЫ ===

SYSTEM_PROMPT = """You are an automotive damage assessor. Analyze the vehicle photo and return ONLY valid JSON, no markdown.

Allowed values:
- type: scratch|dent|crack|deformation|corrosion|other
- severity: cosmetic|minor|moderate|severe|critical
- repair_method (Russian): полировка|PDR|покраска детали|покраска+рихтовка|замена детали

Costs in RUB: polish 2000-8000, PDR 3000-15000, paint 8000-25000, replace 15000-60000, glass 10000-40000.

Return this JSON structure:
{
  "vehicle": {"make": "string or null", "model": "string or null", "year": number or null, "confidence": number 0-100},
  "damages": [
    {
      "type": "scratch|dent|crack|deformation|corrosion|other",
      "location": "part name in Russian",
      "severity": "cosmetic|minor|moderate|severe|critical",
      "size_cm2": number or null,
      "description": "in Russian",
      "repair_method": "полировка|PDR|покраска детали|покраска+рихтовка|замена детали",
      "estimated_cost_rub": {"min": number, "max": number}
    }
  ],
  "summary": {
    "total_estimated_cost": {"min": number, "max": number},
    "repair_time_days": "e.g. 3-5 дней",
    "drivable": true or false or null,
    "recommendations": ["in Russian"],
    "requires_in_person_inspection": true or false
  },
  "meta": {
    "photo_quality": "good|fair|poor",
    "missing_angles": ["missing angle in Russian"],
    "analysis_confidence": number 0-100
  }
}

If the photo does NOT contain a vehicle, return exactly this JSON:
{"error": "no_vehicle", "message": "На фото не обнаружено транспортное средство"}"""


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
    if not raw:
        return "{}"
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


# === AUTH ХЕЛПЕРЫ ===

async def send_otp_email(to_email: str, code: str):
    if not SMTP_USER or not SMTP_PASS:
        return  # SMTP не настроен — код виден только в логах
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{code} — код входа в АвтоСкан"
    msg["From"]    = f"АвтоСкан <{SMTP_FROM}>"
    msg["To"]      = to_email
    html = f"""<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px 16px">
  <div style="font-size:22px;font-weight:400;letter-spacing:-1px;margin-bottom:8px">
    АВТО<span style="color:#FF5722">СКАН</span>
  </div>
  <p style="color:#888;font-size:13px;margin-bottom:32px">Анализ повреждений автомобиля</p>
  <p style="font-size:15px;margin-bottom:16px">Ваш код для входа:</p>
  <div style="font-size:36px;font-weight:600;letter-spacing:8px;background:#f5f5f5;padding:20px 24px;display:inline-block;margin-bottom:16px">{code}</div>
  <p style="font-size:13px;color:#888">Код действителен 10 минут.<br>Если вы не запрашивали код — просто проигнорируйте это письмо.</p>
</div>"""
    msg.attach(MIMEText(html, "html"))
    try:
        await aiosmtplib.send(msg, hostname=SMTP_HOST, port=SMTP_PORT,
                              username=SMTP_USER, password=SMTP_PASS, use_tls=True)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ошибка отправки письма: {e}")


async def resolve_session(token: Optional[str], pool) -> Optional[dict]:
    if not token:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT s.user_id, p.email, p.created_at
               FROM sessions s JOIN profiles p ON s.user_id = p.id
               WHERE s.token = $1 AND s.expires_at > NOW()""",
            token
        )
        if not row:
            return None
        await conn.execute("UPDATE sessions SET last_used = NOW() WHERE token = $1", token)
    return {"id": row["user_id"], "email": row["email"],
            "created_at": row["created_at"].isoformat()}


# === ЭНДПОИНТЫ ===

@app.get("/health")
async def health():
    return {"status": "ok", "version": "3.0.0", "timestamp": datetime.now().isoformat()}


@app.post("/auth/send-code")
async def auth_send_code(data: SendCodeRequest):
    email = data.email.lower().strip()
    async with app.state.pool.acquire() as conn:
        recent = await conn.fetchrow(
            "SELECT created_at FROM auth_codes WHERE email=$1 AND created_at > NOW()-INTERVAL '60 seconds' LIMIT 1",
            email
        )
        if recent:
            raise HTTPException(status_code=429, detail="Подождите минуту перед повторной отправкой")
        code = str(random.randint(100000, 999999))
        expires_at = datetime.now() + timedelta(minutes=10)
        await conn.execute(
            "INSERT INTO auth_codes (email, code, expires_at) VALUES ($1, $2, $3)",
            email, code, expires_at
        )
    await send_otp_email(email, code)
    return {"ok": True}


@app.post("/auth/verify-code")
async def auth_verify_code(data: VerifyCodeRequest):
    email = data.email.lower().strip()
    async with app.state.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM auth_codes WHERE email=$1 AND code=$2 AND expires_at>NOW() AND used=FALSE ORDER BY created_at DESC LIMIT 1",
            email, data.code.strip()
        )
        if not row:
            raise HTTPException(status_code=400, detail="Неверный или устаревший код")
        await conn.execute("UPDATE auth_codes SET used=TRUE WHERE id=$1", row["id"])
        profile = await conn.fetchrow("SELECT id FROM profiles WHERE email=$1", email)
        if not profile:
            user_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO profiles (id, email, consent_given) VALUES ($1, $2, TRUE)",
                user_id, email
            )
        else:
            user_id = profile["id"]
            await conn.execute("UPDATE profiles SET consent_given=TRUE WHERE id=$1", user_id)
        token = secrets.token_hex(32)
        expires_at = datetime.now() + timedelta(days=30)
        await conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES ($1, $2, $3)",
            token, user_id, expires_at
        )
    return {"token": token, "user_id": user_id, "email": email}


@app.get("/auth/me")
async def auth_me(x_session_token: Optional[str] = Header(None)):
    user = await resolve_session(x_session_token, app.state.pool)
    if not user:
        return {"user": None}
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchrow("SELECT * FROM profiles WHERE id=$1", user["id"])
    return {"user": {**user, **(dict(profile) if profile else {})}}


@app.post("/auth/logout")
async def auth_logout(x_session_token: Optional[str] = Header(None)):
    if x_session_token:
        async with app.state.pool.acquire() as conn:
            await conn.execute("DELETE FROM sessions WHERE token=$1", x_session_token)
    return {"ok": True}


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
    x_session_token: Optional[str] = Header(None),
):
    """Анализ фото. Гость — без токена, авторизованный — с x-session-token."""
    current_user = await resolve_session(x_session_token, app.state.pool)

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
            max_tokens=8000,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": get_user_prompt(inspection_type)},
                    {"type": "image_url", "image_url": {"url": f"data:{file.content_type};base64,{base64_image}"}}
                ]}
            ]
        )

        finish_reason = response.choices[0].finish_reason if response.choices else None
        raw = response.choices[0].message.content if response.choices else None
        if not raw:
            detail = "Модель вернула пустой ответ"
            if finish_reason == "length":
                detail = "Модель вернула пустой ответ (превышен лимит токенов)"
            raise HTTPException(status_code=502, detail=detail)
        cleaned = clean_json(raw)

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            raise HTTPException(status_code=502, detail=f"Невалидный JSON от модели: {raw[:200]}")

        if parsed.get("error") == "no_vehicle":
            raise HTTPException(status_code=422, detail=parsed.get("message", "На фото не обнаружено транспортное средство"))

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
        if current_user:
            async with app.state.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO analyses (user_id, result, photo_name, inspection_type, photo_data, photo_mime)
                    VALUES ($1, $2, $3, $4, $5, $6)
                """, current_user["id"], json.dumps(result), file.filename, inspection_type,
                     base64_image, file.content_type)
                await conn.execute(
                    "UPDATE profiles SET analyses_count = analyses_count + 1 WHERE id = $1",
                    current_user["id"]
                )

        return JSONResponse(result)

    except HTTPException:
        raise
    except Exception as e:
        if file_path.exists():
            file_path.unlink()
        raise HTTPException(status_code=502, detail=f"Ошибка нейросети: {str(e)}")


class AnalysisSaveRequest(BaseModel):
    user_id: str
    result: dict
    photo_name: Optional[str] = None
    inspection_type: Optional[str] = None


@app.post("/analysis/save")
async def save_pending_analysis(data: AnalysisSaveRequest):
    """Сохраняет результат анализа, сделанного гостем, после его авторизации."""
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchrow(
            "SELECT email FROM profiles WHERE id = $1",
            data.user_id
        )
        if not profile:
            raise HTTPException(status_code=404, detail="Профиль не найден")

        await conn.execute("""
            INSERT INTO analyses (user_id, result, photo_name, inspection_type)
            VALUES ($1, $2, $3, $4)
        """, data.user_id, json.dumps(data.result),
             data.photo_name, data.inspection_type or "страховой случай")

        await conn.execute("""
            UPDATE profiles SET analyses_count = analyses_count + 1 WHERE id = $1
        """, data.user_id)

    return {"ok": True}


@app.post("/payment/create")
async def create_payment(data: PaymentCreateRequest):
    """Создаёт платёж YooKassa и возвращает URL для оплаты."""
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET:
        raise HTTPException(status_code=503, detail="Оплата временно недоступна")

    ptype = "pdf"
    price = YOOKASSA_PDF_PRICE
    desc  = "Скачивание PDF-отчёта об оценке повреждений автомобиля"

    idempotency_key = str(uuid.uuid4())
    payment_body = {
        "amount": {"value": price, "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "return_url": YOOKASSA_RETURN_URL.rstrip('/') + "/?payment=success"
        },
        "capture": True,
        "description": desc,
        "metadata": {"user_id": data.user_id or "", "type": ptype},
    }
    if data.customer_email:
        payment_body["receipt"] = {
            "customer": {"email": data.customer_email},
            "items": [{
                "description": desc,
                "quantity": "1.00",
                "amount": {"value": price, "currency": "RUB"},
                "vat_code": 1,  # без НДС (ИП на УСН)
                "payment_mode": "full_payment",
                "payment_subject": "service",
            }]
        }
    try:
        payment = YKPayment.create(payment_body, idempotency_key)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ошибка ЮКасса: {e}")

    if data.user_id:
        async with app.state.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO payments (id, user_id, status, amount, type)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (id) DO NOTHING
            """, payment.id, data.user_id, payment.status, float(price), ptype)

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
        meta       = obj.get("metadata", {})
        user_id    = meta.get("user_id")
        ptype      = meta.get("type", "analysis")
        if user_id:
            async with app.state.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE profiles SET pdf_credits = pdf_credits + 1 WHERE id = $1", user_id
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
