"""
backend/main.py
Главный файл бэкенда — API для загрузки фото и анализа нейросетью.
Production-ready версия с валидацией и структурированным выводом.
Совместим с FastAPI 0.109.0 + Pydantic v2
"""

import base64
import json
import os
import configparser
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Literal

import httpx
import openai
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator  # ← ИЗМЕНЕНО: field_validator вместо validator


# === КОНФИГУРАЦИЯ ===

config = configparser.ConfigParser()
config.read('config.ini')

if not config.sections():
    raise FileNotFoundError(
        "config.ini не найден! Скопируйте config.example.ini в config.ini и заполните ключи."
    )

YANDEX_API_KEY = config.get('yandex', 'api_key')
YANDEX_FOLDER = config.get('yandex', 'folder_id')
YANDEX_MODEL = config.get('yandex', 'model', fallback='yandexgpt-32k')

UPLOAD_DIR = Path(os.getenv('UPLOAD_DIR', '/app/uploads'))
UPLOAD_DIR.mkdir(exist_ok=True)


# === МОДЕЛИ ДАННЫХ (Pydantic v2) ===

class VehicleInfo(BaseModel):
    make: Optional[str] = None
    model: Optional[str] = None
    year: Optional[int] = None
    confidence: int = Field(ge=0, le=100, default=0)
    
    @field_validator('year')  # ← ИЗМЕНЕНО: field_validator + @classmethod
    @classmethod
    def validate_year(cls, v):
        if v is not None and (v < 1950 or v > datetime.now().year + 1):
            return None
        return v


class CostRange(BaseModel):
    min: int = Field(ge=0)
    max: int = Field(ge=0)
    
    @field_validator('max')  # ← ИЗМЕНЕНО: field_validator + @classmethod
    @classmethod
    def max_gte_min(cls, v, info):  # ← ИЗМЕНЕНО: info вместо values
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


# === ПРОМПТЫ ===

SYSTEM_PROMPT = """Ты — сертифицированный оценщик кузовного ремонта категории А с 15-летним стажем. Твоя задача — дать профессиональную оценку повреждений по фото для страховой компании.

ПРАВИЛА АНАЛИЗА:
1. Если не видишь повреждений чётко — пиши "требуется осмотр", не гадай
2. Различай "царапина" (<0.1мм глубина) vs "вмятина" (деформация) vs "трещина/разрыв"
3. Указывай примерную площадь повреждения в см²
4. Для каждого повреждения оценивай критичность: косметическая/функциональная/безопасность
5. Если на фото несколько ракурсов — оцени каждый

ТИПЫ ПОВРЕЖДЕНИЙ:
- scratch: поверхностная царапина, не глубже лака
- dent: вмятина без нарушения лакокрасочного покрытия
- crack: трещина, скол, разрыв металла/пластика
- deformation: деформация детали с нарушением геометрии
- corrosion: ржавчина, коррозия
- other: другое (опиши в description)

УРОВНИ СЕРЬЕЗНОСТИ:
- cosmetic: только внешний вид, не влияет на функцию
- minor: небольшое повреждение, локальный ремонт
- moderate: требует серьёзного ремонта детали
- severe: требует замены детали или сложного восстановления
- critical: опасно для безопасности, авто не на ходу

СПОСОБЫ РЕМОНТА:
- полировка: для мелких царапин
- PDR: беспокрасочное удаление вмятин
- покраска детали: локальная покраска
- покраска+рихтовка: восстановление геометрии и покраска
- замена детали: полная замена

ФОРМАТ ОТВЕТА (строго JSON, без markdown):
{
    "vehicle": {
        "make": "строка или null",
        "model": "строка или null",
        "year": число или null,
        "confidence": число 0-100
    },
    "damages": [
        {
            "type": "scratch|dent|crack|deformation|corrosion|other",
            "location": "конкретная деталь (например: 'переднее левое крыло')",
            "severity": "cosmetic|minor|moderate|severe|critical",
            "size_cm2": число или null,
            "description": "что видно на фото",
            "repair_method": "полировка|PDR|покраска детали|покраска+рихтовка|замена детали",
            "estimated_cost_rub": {"min": число, "max": число}
        }
    ],
    "summary": {
        "total_estimated_cost": {"min": число, "max": число},
        "repair_time_days": "примерный срок (например: '2-3 дня')",
        "drivable": true/false/null,
        "recommendations": ["список действий"],
        "requires_in_person_inspection": true/false
    },
    "meta": {
        "photo_quality": "good|fair|poor",
        "missing_angles": ["каких ракурсов не хватает"],
        "analysis_confidence": число 0-100
    }
}"""


def get_user_prompt(inspection_type: str = "страховой случай") -> str:
    return f"""Проанализируй фото автомобиля.

КОНТЕКСТ ОСМОТРА:
- Дата: {datetime.now().strftime("%Y-%m-%d")}
- Тип осмотра: {inspection_type}

ВАЖНО:
- Если на фото несколько углов — оцени каждый
- Если видны номера/VIN — зафиксируй
- Отметь скрытые повреждения, которые могут быть при таком характере удара
- Если не уверен — укажи низкую confidence и требование осмотра

Ответ дай ТОЛЬКО в JSON, без markdown-разметки, без ```."""


# === ИНИЦИАЛИЗАЦИЯ YANDEX GPT ===

http_client = httpx.Client(timeout=60.0)

client = openai.OpenAI(
    api_key=YANDEX_API_KEY,
    base_url="https://llm.api.cloud.yandex.net/v1",
    http_client=http_client,
)


# === FASTAPI ПРИЛОЖЕНИЕ ===

app = FastAPI(
    title="Car Damage Analyzer Pro",
    description="API для профессионального анализа повреждений автомобилей через YandexGPT",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """Проверка работоспособности."""
    return {
        "status": "ok",
        "model": YANDEX_MODEL,
        "version": "2.0.0",
        "timestamp": datetime.now().isoformat()
    }


def clean_json_response(raw_content: str) -> str:
    """Очистка ответа модели от markdown-разметки."""
    if raw_content.startswith("```"):
        parts = raw_content.split("```")
        if len(parts) >= 3:
            raw_content = parts[1]
            if raw_content.startswith("json"):
                raw_content = raw_content[4:]
    
    raw_content = raw_content.strip()
    
    start_idx = raw_content.find('{')
    end_idx = raw_content.rfind('}')
    
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        raw_content = raw_content[start_idx:end_idx+1]
    
    return raw_content


@app.post("/analyze")
async def analyze_image(
    file: UploadFile = File(...),
    inspection_type: str = "страховой случай"
):
    """
    Принимает фото, отправляет в YandexGPT, возвращает структурированный анализ.
    """
    
    # Валидация файла
    allowed_types = ['image/jpeg', 'image/png', 'image/webp']
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Неподдерживаемый формат. Разрешены: {allowed_types}"
        )
    
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
    
    # Кодирование в base64
    base64_image = base64.b64encode(contents).decode('utf-8')
    mime_type = file.content_type
    
    # Запрос к Yandex GPT
    try:
        response = client.chat.completions.create(
            model=f"gpt://{YANDEX_FOLDER}/{YANDEX_MODEL}",
            temperature=0.1,
            max_tokens=2000,
            messages=[
                {
                    "role": "system",
                    "content": [{"type": "text", "text": SYSTEM_PROMPT}]
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": get_user_prompt(inspection_type)
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{base64_image}"
                            }
                        }
                    ]
                }
            ]
        )
        
        raw_content = response.choices[0].message.content
        cleaned_json = clean_json_response(raw_content)
        
        try:
            parsed_data = json.loads(cleaned_json)
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=502,
                detail=f"Модель вернула невалидный JSON. Raw: {raw_content[:200]}..."
            )
        
        # Валидируем через Pydantic
        try:
            validated = AnalysisResult(**parsed_data)
        except Exception as e:
            # Если валидация не прошла, возвращаем как есть с warning
            return JSONResponse({
                "success": True,
                "data": parsed_data,
                "validation_warning": str(e),
                "model_used": YANDEX_MODEL,
                "tokens_used": response.usage.total_tokens if response.usage else None,
                "processed_at": datetime.now().isoformat(),
                "filename": filename
            })
        
        return JSONResponse({
            "success": True,
            "data": validated.model_dump(),  # ← ИЗМЕНЕНО: model_dump() вместо dict()
            "model_used": YANDEX_MODEL,
            "tokens_used": response.usage.total_tokens if response.usage else None,
            "processed_at": datetime.now().isoformat(),
            "filename": filename
        })
        
    except HTTPException:
        raise
    except Exception as e:
        if file_path.exists():
            file_path.unlink()
        
        raise HTTPException(
            status_code=502,
            detail=f"Ошибка при обращении к нейросети: {str(e)}"
        )


@app.get("/history")
async def get_analysis_history(limit: int = 10):
    """
    Получение списка последних загруженных файлов (без анализа).
    """
    files = sorted(UPLOAD_DIR.glob("*.jpg")) + \
            sorted(UPLOAD_DIR.glob("*.jpeg")) + \
            sorted(UPLOAD_DIR.glob("*.png")) + \
            sorted(UPLOAD_DIR.glob("*.webp"))
    
    files = sorted(files, key=lambda x: x.stat().st_mtime, reverse=True)[:limit]
    
    return {
        "files": [
            {
                "filename": f.name,
                "uploaded_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                "size_bytes": f.stat().st_size
            }
            for f in files
        ]
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)