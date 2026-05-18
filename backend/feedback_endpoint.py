# ── Добавь в main.py ────────────────────────────────────────
# Вставь после класса EventLog и его эндпоинта

class FeedbackData(BaseModel):
    type: str          # bug / idea / access / other
    text: str
    email: Optional[str] = None
    user_id: Optional[str] = None
    url: Optional[str] = None


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
