-- ============================================================
-- Carscan — Аналитические запросы (выполнять в psql)
-- ============================================================

-- 1. ДАШБОРД: ключевые метрики за последние 30 дней
SELECT
  COUNT(*) FILTER (WHERE event = 'page_view')          AS просмотров,
  COUNT(*) FILTER (WHERE event = 'analyze_started')    AS анализов_запущено,
  COUNT(*) FILTER (WHERE event = 'analyze_success')    AS анализов_успешно,
  COUNT(*) FILTER (WHERE event = 'analyze_error')      AS ошибок,
  COUNT(*) FILTER (WHERE event = 'paywall_shown')      AS упёрлись_в_лимит,
  COUNT(DISTINCT user_id) FILTER (WHERE event = 'page_view') AS уникальных_пользователей
FROM events
WHERE created_at > now() - interval '30 days';


-- 2. ВОРОНКА конверсии
SELECT
  COUNT(*) FILTER (WHERE event = 'page_view')          AS "1. Зашли",
  COUNT(*) FILTER (WHERE event = 'photo_uploaded')     AS "2. Загрузили фото",
  COUNT(*) FILTER (WHERE event = 'analyze_started')    AS "3. Запустили анализ",
  COUNT(*) FILTER (WHERE event = 'analyze_success')    AS "4. Получили результат",
  COUNT(*) FILTER (WHERE event = 'paywall_shown')      AS "5. Упёрлись в лимит"
FROM events
WHERE created_at > now() - interval '30 days';


-- 3. АКТИВНЫЕ ПОЛЬЗОВАТЕЛИ по дням (DAU)
SELECT
  date_trunc('day', created_at)::date AS день,
  COUNT(DISTINCT user_id) AS активных
FROM events
WHERE event = 'page_view'
  AND created_at > now() - interval '30 days'
GROUP BY день
ORDER BY день DESC;


-- 4. НОВЫЕ ПОЛЬЗОВАТЕЛИ по дням
SELECT
  date_trunc('day', created_at)::date AS день,
  COUNT(*) AS новых
FROM profiles
WHERE created_at > now() - interval '30 days'
GROUP BY день
ORDER BY день DESC;


-- 5. АНАЛИЗЫ — статистика по качеству фото
SELECT
  result->'data'->'meta'->>'photo_quality' AS качество_фото,
  COUNT(*) AS анализов,
  ROUND(AVG((result->'data'->'meta'->>'analysis_confidence')::numeric), 1) AS средняя_уверенность
FROM analyses
WHERE created_at > now() - interval '30 days'
GROUP BY качество_фото;


-- 6. ПОВРЕЖДЕНИЯ — самые частые типы
SELECT
  dmg->>'type' AS тип,
  dmg->>'severity' AS серьёзность,
  COUNT(*) AS встречается
FROM analyses,
     jsonb_array_elements(result->'data'->'damages') AS dmg
WHERE created_at > now() - interval '30 days'
GROUP BY тип, серьёзность
ORDER BY встречается DESC
LIMIT 15;


-- 7. СРЕДНЯЯ СТОИМОСТЬ ремонта
SELECT
  ROUND(AVG((result->'data'->'summary'->'total_estimated_cost'->>'min')::numeric)) AS средний_минимум,
  ROUND(AVG((result->'data'->'summary'->'total_estimated_cost'->>'max')::numeric)) AS средний_максимум
FROM analyses
WHERE created_at > now() - interval '30 days'
  AND result->'data'->'summary'->'total_estimated_cost' IS NOT NULL;


-- 8. RETENTION — пользователи сделавшие >1 анализа
SELECT
  analyses_count AS анализов,
  COUNT(*) AS пользователей
FROM profiles
GROUP BY анализов
ORDER BY анализов;


-- 9. ОШИБКИ — последние 20
SELECT created_at, params->>'message' AS ошибка, ua
FROM events
WHERE event = 'analyze_error'
ORDER BY created_at DESC
LIMIT 20;


-- 10. ПОЛЬЗОВАТЕЛИ близкие к лимиту (для таргетинга)
SELECT email, analyses_count, is_paid, created_at
FROM profiles
WHERE analyses_count >= 1 AND is_paid = FALSE
ORDER BY created_at DESC;
