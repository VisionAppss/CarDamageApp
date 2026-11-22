"""
backend/main.py
FastAPI бэкенд с подключением к PostgreSQL.
Хранит профили пользователей, историю анализов и события аналитики.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import uuid
import secrets
import random
import time
import configparser

logger = logging.getLogger("uvicorn.error")
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional, Literal

import httpx
import openai
import asyncpg
import aiosmtplib
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Header, Request, Response, Depends
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
YANDEX_MODEL   = config.get('yandex', 'model', fallback='qwen3.6-35b-a3b/latest')

# SMTP для OTP-авторизации
SMTP_HOST = config.get('smtp', 'host',     fallback='smtp.yandex.ru')
SMTP_PORT = int(config.get('smtp', 'port', fallback='465'))
SMTP_USER = config.get('smtp', 'user',     fallback='') or os.getenv('SMTP_USER', '')
SMTP_PASS = config.get('smtp', 'password', fallback='') or os.getenv('SMTP_PASSWORD', '')
SMTP_FROM = config.get('smtp', 'from',     fallback=SMTP_USER)

UPLOAD_DIR = Path(os.getenv('UPLOAD_DIR', '/app/uploads'))
UPLOAD_DIR.mkdir(exist_ok=True)

ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', '')
ADMIN_PASSWORD_HASH = os.getenv('ADMIN_PASSWORD_HASH', '')
ADMIN_SESSION_COOKIE = 'carscan_admin_session'
ADMIN_COOKIE_PATH = '/api/admin/'
ADMIN_SESSION_SECONDS = 3600

try:
    salt_hex, digest_hex = ADMIN_PASSWORD_HASH.split(':')
    ADMIN_SALT = bytes.fromhex(salt_hex)
    ADMIN_DIGEST = bytes.fromhex(digest_hex)
    if len(ADMIN_SALT) != 16 or len(ADMIN_DIGEST) != 32:
        raise ValueError('Invalid admin password hash length')
except ValueError:
    ADMIN_SALT = ADMIN_DIGEST = b''
    if ADMIN_PASSWORD_HASH:
        logger.error('Invalid ADMIN_PASSWORD_HASH format')

# YooKassa
YOOKASSA_SHOP_ID     = config.get('yookassa', 'shop_id',        fallback='') or os.getenv('YOOKASSA_SHOP_ID', '')
YOOKASSA_SECRET      = config.get('yookassa', 'secret_key',     fallback='') or os.getenv('YOOKASSA_SECRET_KEY', '')
YOOKASSA_ANALYSIS_PRICE = config.get('yookassa', 'analysis_price', fallback='149.00') or os.getenv('YOOKASSA_ANALYSIS_PRICE', '149.00')
REPORT_PRICE = "100.00"
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


class BBox(BaseModel):
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    w: float = Field(ge=0, le=100)
    h: float = Field(ge=0, le=100)


class DamageItem(BaseModel):
    type: Literal["scratch", "dent", "crack", "deformation", "corrosion", "other"]
    location: str
    severity: Literal["cosmetic", "minor", "moderate", "severe", "critical"]
    size_cm2: Optional[float] = Field(None, ge=0)
    description: str
    repair_method: str
    estimated_cost_rub: CostRange
    bbox: Optional[BBox] = None


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
    report_id: Optional[uuid.UUID] = None
    checkout_id: Optional[uuid.UUID] = None


class ReportAccessRequest(BaseModel):
    report_id: uuid.UUID
    payment_id: Optional[str] = None

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
- bbox: bounding box of the damage area as percentages of image width/height (x,y = top-left corner, w,h = size). All values 0-100.

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
      "estimated_cost_rub": {"min": number, "max": number},
      "bbox": {"x": number, "y": number, "w": number, "h": number}
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
    async with app.state.pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS analysis_reports (
                id UUID PRIMARY KEY,
                result JSONB NOT NULL,
                paid_at TIMESTAMPTZ
            )
        """)


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

    raw_bbox = d.get('bbox')
    bbox = None
    if isinstance(raw_bbox, dict):
        try:
            bbox = {
                'x': float(raw_bbox['x']),
                'y': float(raw_bbox['y']),
                'w': float(raw_bbox['w']),
                'h': float(raw_bbox['h']),
            }
        except (KeyError, TypeError, ValueError):
            bbox = None

    return {
        'type': d.get('type', 'other'),
        'location': d.get('location', ''),
        'severity': d.get('severity', 'moderate'),
        'size_cm2': size,
        'description': d.get('description', ''),
        'repair_method': method,
        'estimated_cost_rub': cost,
        'bbox': bbox,
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
    msg["Subject"] = f"{code} — код входа в Carscan"
    msg["From"]    = f"АвтоСкан <{SMTP_FROM}>"
    msg["To"]      = to_email
    html = f"""<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px 16px">
  <div style="font-size:22px;font-weight:400;letter-spacing:-1px;margin-bottom:8px">
    CAR<span style="color:#FF5722">SCAN</span>
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


class AdminLogin(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


def sign_admin_session(payload: str) -> str:
    return hmac.new(ADMIN_DIGEST, f'admin:{ADMIN_USERNAME}:{payload}'.encode(), 'sha256').hexdigest()


async def require_admin(request: Request) -> None:
    token = request.cookies.get(ADMIN_SESSION_COOKIE, '')
    if ADMIN_USERNAME and ADMIN_DIGEST and len(token) <= 128:
        expires, _, signature = token.partition('.')
        if expires.isascii() and expires.isdecimal() and int(expires) > time.time():
            if secrets.compare_digest(signature.encode(), sign_admin_session(expires).encode()):
                return
    raise HTTPException(status_code=403, detail="Доступ запрещён")


@app.post("/admin/login")
async def admin_login(data: AdminLogin, response: Response):
    if not ADMIN_USERNAME or not ADMIN_DIGEST:
        raise HTTPException(status_code=503, detail="Вход администратора не настроен")

    digest = await asyncio.to_thread(
        hashlib.scrypt, data.password.encode(), salt=ADMIN_SALT, n=32768, r=8, p=1,
        maxmem=64 * 1024 * 1024, dklen=32,
    )
    username_matches = secrets.compare_digest(data.username.encode(), ADMIN_USERNAME.encode())
    password_matches = secrets.compare_digest(digest, ADMIN_DIGEST)
    if not (username_matches and password_matches):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    expires = str(int(time.time()) + ADMIN_SESSION_SECONDS)
    response.set_cookie(
        ADMIN_SESSION_COOKIE, f'{expires}.{sign_admin_session(expires)}',
        max_age=ADMIN_SESSION_SECONDS, path=ADMIN_COOKIE_PATH,
        secure=True, httponly=True, samesite='strict',
    )
    response.headers['Cache-Control'] = 'no-store'
    return {"ok": True}


@app.post("/admin/logout")
async def admin_logout(response: Response):
    response.delete_cookie(
        ADMIN_SESSION_COOKIE, path=ADMIN_COOKIE_PATH, secure=True, httponly=True, samesite='strict',
    )
    response.headers['Cache-Control'] = 'no-store'
    return {"ok": True}


# === АНАЛИТИКА (см. backend/analytics_queries.sql) ===

ANALYTICS_QUERIES = [
    ("dashboard_30d", "Дашборд: ключевые метрики за 30 дней", """
        SELECT
          COUNT(*) FILTER (WHERE event = 'page_view')          AS "просмотров",
          COUNT(*) FILTER (WHERE event = 'analyze_started')    AS "анализов_запущено",
          COUNT(*) FILTER (WHERE event = 'analyze_success')    AS "анализов_успешно",
          COUNT(*) FILTER (WHERE event = 'analyze_error')      AS "ошибок",
          COUNT(*) FILTER (WHERE event = 'paywall_shown')      AS "упёрлись_в_лимит",
          COUNT(DISTINCT user_id) FILTER (WHERE event = 'page_view') AS "уникальных_пользователей"
        FROM events
        WHERE created_at > now() - interval '30 days'
    """),
    ("funnel_30d", "Воронка конверсии", """
        SELECT
          COUNT(*) FILTER (WHERE event = 'page_view')          AS "1. Зашли",
          COUNT(*) FILTER (WHERE event = 'photo_uploaded')     AS "2. Загрузили фото",
          COUNT(*) FILTER (WHERE event = 'analyze_started')    AS "3. Запустили анализ",
          COUNT(*) FILTER (WHERE event = 'analyze_success')    AS "4. Получили результат",
          COUNT(*) FILTER (WHERE event = 'paywall_shown')      AS "5. Упёрлись в лимит"
        FROM events
        WHERE created_at > now() - interval '30 days'
    """),
    ("dau", "Активные пользователи по дням (DAU)", """
        SELECT
          date_trunc('day', created_at)::date AS "день",
          COUNT(DISTINCT user_id) AS "активных"
        FROM events
        WHERE event = 'page_view'
          AND created_at > now() - interval '30 days'
        GROUP BY 1
        ORDER BY 1 DESC
    """),
    ("new_users", "Новые пользователи по дням", """
        SELECT
          date_trunc('day', created_at)::date AS "день",
          COUNT(*) AS "новых"
        FROM profiles
        WHERE created_at > now() - interval '30 days'
        GROUP BY 1
        ORDER BY 1 DESC
    """),
    ("photo_quality", "Анализы: статистика по качеству фото", """
        SELECT
          result->'data'->'meta'->>'photo_quality' AS "качество_фото",
          COUNT(*) AS "анализов",
          ROUND(AVG((result->'data'->'meta'->>'analysis_confidence')::numeric), 1) AS "средняя_уверенность"
        FROM analyses
        WHERE created_at > now() - interval '30 days'
        GROUP BY 1
    """),
    ("top_damages", "Повреждения: самые частые типы", """
        SELECT
          dmg->>'type' AS "тип",
          dmg->>'severity' AS "серьёзность",
          COUNT(*) AS "встречается"
        FROM analyses,
             jsonb_array_elements(result->'data'->'damages') AS dmg
        WHERE created_at > now() - interval '30 days'
        GROUP BY 1, 2
        ORDER BY 3 DESC
        LIMIT 15
    """),
    ("avg_repair_cost", "Средняя стоимость ремонта", """
        SELECT
          ROUND(AVG((result->'data'->'summary'->'total_estimated_cost'->>'min')::numeric)) AS "средний_минимум",
          ROUND(AVG((result->'data'->'summary'->'total_estimated_cost'->>'max')::numeric)) AS "средний_максимум"
        FROM analyses
        WHERE created_at > now() - interval '30 days'
          AND result->'data'->'summary'->'total_estimated_cost' IS NOT NULL
    """),
    ("retention", "Retention: пользователи сделавшие >1 анализа", """
        SELECT
          analyses_count AS "анализов",
          COUNT(*) AS "пользователей"
        FROM profiles
        GROUP BY 1
        ORDER BY 1
    """),
    ("recent_errors", "Последние 20 ошибок", """
        SELECT created_at, params->>'message' AS "ошибка", ua
        FROM events
        WHERE event = 'analyze_error'
        ORDER BY created_at DESC
        LIMIT 20
    """),
    ("near_limit_users", "Пользователи близкие к лимиту", """
        SELECT email, analyses_count, is_paid, created_at
        FROM profiles
        WHERE analyses_count >= 1 AND is_paid = FALSE
        ORDER BY created_at DESC
    """),
    ("payments_summary", "Выручка за 30 дней (по статусу и типу)", """
        SELECT
          status AS "статус",
          type AS "тип",
          COUNT(*) AS "платежей",
          SUM(amount) AS "сумма_руб"
        FROM payments
        WHERE created_at > now() - interval '30 days'
        GROUP BY status, type
        ORDER BY status, type
    """),
]


@app.get("/admin/analytics", dependencies=[Depends(require_admin)])
async def admin_analytics(response: Response):
    """Выполняет все аналитические запросы и возвращает результат для админ-панели."""
    sections = []
    async with app.state.pool.acquire() as conn:
        for key, title, sql in ANALYTICS_QUERIES:
            rows = await conn.fetch(sql)
            sections.append({
                "key": key,
                "title": title,
                "rows": [dict(r) for r in rows],
            })
    response.headers['Cache-Control'] = 'no-store'
    return {"sections": sections}


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
async def get_analyses(user_id: str, limit: int = 20, x_session_token: Optional[str] = Header(None)):
    """История анализов пользователя."""
    user = await resolve_session(x_session_token, app.state.pool)
    if not user or user["id"] != user_id:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    async with app.state.pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT a.id, a.created_at, a.inspection_type, a.photo_name,
                   COALESCE(r.result, a.result) AS result, a.photo_data, a.photo_mime
            FROM analyses a
            LEFT JOIN analysis_reports r ON r.id::text = a.result->>'report_id' AND r.paid_at IS NOT NULL
            WHERE a.user_id = $1
            ORDER BY a.created_at DESC
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

    # Запрос к YandexGPT (retry 2 раза при connection errors)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": get_user_prompt(inspection_type)},
            {"type": "image_url", "image_url": {"url": f"data:{file.content_type};base64,{base64_image}"}}
        ]}
    ]
    last_exc = None
    response = None
    for attempt in range(3):
        try:
            response = ai_client.chat.completions.create(
                model=f"gpt://{YANDEX_FOLDER}/{YANDEX_MODEL}",
                temperature=0.1,
                max_tokens=8000,
                messages=messages,
            )
            break
        except openai.APIConnectionError as e:
            last_exc = e
            if attempt < 2:
                time.sleep(2 ** attempt)
            continue
        except openai.APIStatusError as e:
            status = e.status_code
            raw_body = e.response.text if hasattr(e, 'response') and e.response else str(e)
            logger.error(f"YandexGPT API error {status}: {raw_body}")
            if status == 401:
                raise HTTPException(status_code=502, detail="Ошибка авторизации нейросети: проверьте API-ключ")
            if status == 429:
                raise HTTPException(status_code=502, detail="Превышен лимит запросов к нейросети, попробуйте позже")
            if status == 503:
                raise HTTPException(status_code=502, detail="Нейросеть временно недоступна, попробуйте позже")
            raise HTTPException(status_code=502, detail=f"Ошибка нейросети: HTTP {status}: {raw_body[:300]}")
        except openai.APITimeoutError:
            raise HTTPException(status_code=504, detail="Нейросеть не ответила вовремя, попробуйте ещё раз")
    if response is None:
        raise HTTPException(status_code=502, detail="Нейросеть недоступна (нет соединения). Попробуйте позже.")

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

    damages = result_data.get("damages") or []
    report_id = uuid.uuid4()
    result.update(report_id=str(report_id), report_unlocked=True)
    preview = {
        **result,
        "report_unlocked": False,
        "damage_count": len(damages),
        "data": {
            "vehicle": result_data.get("vehicle"),
            "damages": [
                {key: damages[0].get(key) for key in ("type", "location", "severity")}
            ] if damages else [],
            "meta": {
                key: (result_data.get("meta") or {}).get(key)
                for key in ("photo_quality", "missing_angles", "analysis_confidence")
            },
        },
    }
    async with app.state.pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO analysis_reports (id, result) VALUES ($1, $2)",
            report_id, json.dumps(result),
        )
        if current_user:
            await conn.execute("""
                INSERT INTO analyses (user_id, result, photo_name, inspection_type, photo_data, photo_mime)
                VALUES ($1, $2, $3, $4, $5, $6)
            """, current_user["id"], json.dumps(preview), file.filename, inspection_type,
                 base64_image, file.content_type)
            await conn.execute(
                "UPDATE profiles SET analyses_count = analyses_count + 1 WHERE id = $1",
                current_user["id"]
            )

    return JSONResponse(preview, headers={"Cache-Control": "no-store"})


class AnalysisSaveRequest(BaseModel):
    user_id: str
    result: dict
    photo_name: Optional[str] = None
    inspection_type: Optional[str] = None


@app.post("/analysis/save")
async def save_pending_analysis(data: AnalysisSaveRequest, x_session_token: Optional[str] = Header(None)):
    """Сохраняет результат анализа, сделанного гостем, после его авторизации."""
    user = await resolve_session(x_session_token, app.state.pool)
    if not user or user["id"] != data.user_id:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
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
    price = REPORT_PRICE
    desc = "Полный отчёт об оценке повреждений автомобиля"
    metadata = {"user_id": data.user_id or "", "type": ptype}
    if data.report_id:
        async with app.state.pool.acquire() as conn:
            report = await conn.fetchrow("SELECT paid_at FROM analysis_reports WHERE id = $1", data.report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Отчёт не найден")
        if report["paid_at"]:
            raise HTTPException(status_code=409, detail="Отчёт уже оплачен")
        metadata.update(type="report", report_id=str(data.report_id), user_id="")

    idempotency_key = str(uuid.uuid4())
    if data.report_id and data.checkout_id:
        idempotency_key = str(uuid.uuid5(data.report_id, str(data.checkout_id)))
    payment_body = {
        "amount": {"value": price, "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "return_url": YOOKASSA_RETURN_URL.rstrip('/') + "/?payment=success"
                          + (f"&report_id={data.report_id}" if data.report_id else "")
        },
        "capture": True,
        "description": desc,
        "metadata": metadata,
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
        payment = await asyncio.to_thread(YKPayment.create, payment_body, idempotency_key)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ошибка ЮКасса: {e}")

    if data.user_id and not data.report_id:
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


@app.post("/report/access")
async def access_report(data: ReportAccessRequest):
    async with app.state.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT result, paid_at FROM analysis_reports WHERE id = $1", data.report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Отчёт не найден")
    if not row["paid_at"]:
        if not data.payment_id:
            raise HTTPException(status_code=402, detail="Отчёт не оплачен")
        if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET:
            raise HTTPException(status_code=503, detail="Проверка оплаты временно недоступна")
        try:
            payment = await asyncio.to_thread(YKPayment.find_one, data.payment_id)
        except Exception:
            raise HTTPException(status_code=502, detail="Не удалось проверить оплату")
        metadata = payment.metadata or {}
        if metadata.get("type") != "report" or metadata.get("report_id") != str(data.report_id):
            raise HTTPException(status_code=403, detail="Платёж относится к другому отчёту")
        if payment.status == "canceled":
            raise HTTPException(status_code=409, detail="Платёж отменён")
        if (payment.status != "succeeded" or not payment.paid
                or payment.amount.currency != "RUB" or payment.amount.value != REPORT_PRICE):
            raise HTTPException(status_code=402, detail="Оплата пока не подтверждена")
        async with app.state.pool.acquire() as conn:
            await conn.execute(
                "UPDATE analysis_reports SET paid_at = NOW() WHERE id = $1 AND paid_at IS NULL", data.report_id,
            )
    return JSONResponse(json.loads(row["result"]), headers={"Cache-Control": "no-store"})


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
        if meta.get("type") == "report":
            try:
                checkout = ReportAccessRequest(report_id=meta.get("report_id"), payment_id=payment_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Некорректные данные платежа")
            await access_report(checkout)
            return {"ok": True}
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
        return {
            "status": payment.status,
            "paid": payment.paid,
            "amount": payment.amount.value,
            "type": (payment.metadata or {}).get("type", "pdf"),
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
