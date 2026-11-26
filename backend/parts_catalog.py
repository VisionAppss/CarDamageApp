"""Deterministic lookup of a confirmed OEM in the current parts price snapshot."""
import csv
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")
WAREHOUSES = {
    "stock_moscow_pechatniki": "Москва, Печатники",
    "stock_moscow_kapotnya": "Москва, Капотня",
}
UNCONFIRMED_PRICE = "Цена детали не подтверждена"
REQUIRED_COLUMNS = {
    "vehicle", "supplier_sku", "oem_raw", "price_rub", "price_type", "snapshot_moscow",
    *WAREHOUSES,
}


def resolve_catalog_path(configured, directory=Path("/app/price-data")):
    if configured:
        return Path(configured)
    catalog = directory / "autobody_carscan_catalog.csv"
    return catalog if catalog.is_file() else directory / "autobody.csv"


def normalize_oem(value):
    stripped = re.sub(r"[ -]", "", value.strip())
    return stripped.upper() if re.fullmatch(r"[a-zA-Z0-9]{3,40}", stripped) else None


def find_quote(path, *, vehicle, oem, now, max_age_hours):
    """Each returned price and warehouse come from a single eligible CSV row."""
    oem = normalize_oem(oem)
    if not oem:
        return None
    latest = {}
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        missing = REQUIRED_COLUMNS.difference(reader.fieldnames or [])
        if missing:
            raise ValueError("Missing required CSV columns: " + ", ".join(sorted(missing)))
        for row in reader:
            if (row.get("vehicle") or "").strip().casefold() != vehicle.strip().casefold():
                continue
            sku = (row.get("supplier_sku") or "").strip()
            if not sku:
                continue
            try:
                snapshot = datetime.fromisoformat(row.get("snapshot_moscow") or "")
            except ValueError:
                continue
            snapshot = snapshot.replace(tzinfo=MOSCOW) if snapshot.tzinfo is None else snapshot.astimezone(MOSCOW)
            previous = latest.get(sku)
            if previous is None or snapshot > previous[0]:
                latest[sku] = (snapshot, row)
            elif snapshot == previous[0] and previous[1] != row:
                # Conflicting entries for the same SKU and timestamp lack a confirmed stock state.
                latest[sku] = (snapshot, None)

    best = None
    for snapshot, row in latest.values():
        if row is None or not timedelta(0) <= now - snapshot <= timedelta(hours=max_age_hours):
            continue
        aliases = {normalize_oem(code) for code in (row.get("oem_raw") or "").split("/")}
        if oem not in aliases:
            continue
        price_type = (row.get("price_type") or "").strip()
        if price_type not in {"wholesale", "retail"}:
            continue
        try:
            price = Decimal(row.get("price_rub") or "")
        except InvalidOperation:
            continue
        if not price.is_finite() or price <= 0:
            continue
        for column, warehouse in WAREHOUSES.items():
            try:
                quantity = int(row.get(column) or "0")
            except ValueError:
                continue
            if quantity > 0 and (best is None or price < best["price_rub"]):
                best = {
                    "price_rub": price,
                    "price_type": price_type,
                    "supplier_sku": row["supplier_sku"],
                    "warehouse": warehouse,
                    "snapshot_moscow": snapshot.isoformat(timespec="minutes"),
                    "source": row.get("source") or "",
                }
    if best:
        best["price_rub"] = str(best["price_rub"])
    return best
