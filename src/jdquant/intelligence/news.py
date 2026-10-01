"""News and corporate events, tied to the market's reaction.

**News** arrives from RSS/Atom feeds you configure (for example your broker's or an exchange's
announcements feed), from the API, or typed in. Each item is matched to instruments by the symbols it
names, recorded as a NEWS market event (so the event graph links what followed it), and can be
examined afterwards: price and volume before and after, and the market events in the window.

**Corporate events** (results, dividends, splits, bonuses, rights issues, board meetings) are entered
or imported as CSV. On the day they are raised as events, and the risk guard can warn about or block
new positions in an instrument with an event today or tomorrow.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
import threading
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from jdquant.core.errors import NotFoundError, ValidationError
from jdquant.intelligence.events import EventStore, MarketEvent

log = logging.getLogger(__name__)

NEWS_KIND, CORP_KIND = "news", "corporate_event"
CORPORATE_KINDS = (
    "RESULTS",
    "DIVIDEND",
    "SPLIT",
    "BONUS",
    "RIGHTS",
    "BOARD_MEETING",
    "AGM",
    "BUYBACK",
    "OTHER",
)
MAX_NEWS = 2000


@dataclass
class NewsItem:
    news_id: str
    at: datetime
    headline: str
    source: str
    url: str = ""
    summary: str = ""
    instruments: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["at"] = self.at.isoformat()
        return d


@dataclass
class CorporateEvent:
    event_id: str
    instrument_id: str
    kind: str
    day: date
    details: str = ""
    source: str = "manual"
    raised: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["day"] = self.day.isoformat()
        return d


def _parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_feed(xml_text: str) -> list[dict[str, Any]]:
    """Items from an RSS 2.0 or Atom document: title, link, time, summary."""
    root = ET.fromstring(xml_text)
    out = []
    atom = "{http://www.w3.org/2005/Atom}"
    for item in root.iter("item"):
        out.append(
            {
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "at": _parse_time(item.findtext("pubDate")),
                "summary": re.sub(r"<[^>]+>", "", item.findtext("description") or "").strip()[:1000],
            }
        )
    for entry in root.iter(f"{atom}entry"):
        link = entry.find(f"{atom}link")
        out.append(
            {
                "title": (entry.findtext(f"{atom}title") or "").strip(),
                "link": link.get("href", "") if link is not None else "",
                "at": _parse_time(entry.findtext(f"{atom}updated") or entry.findtext(f"{atom}published")),
                "summary": re.sub(r"<[^>]+>", "", entry.findtext(f"{atom}summary") or "").strip()[:1000],
            }
        )
    return [i for i in out if i["title"]]


class NewsDesk:
    def __init__(
        self,
        store,
        events,
        event_store: EventStore,
        *,
        instruments: Callable[[], list],
        bars: Callable[[str], list],
        now: Callable[[], datetime],
        http: httpx.Client | None = None,
    ):
        self._store = store
        self._events = events
        self._event_store = event_store
        self._instruments = instruments
        self._bars = bars
        self._now = now
        self._http = http or httpx.Client(timeout=10, follow_redirects=True)
        self.feeds: list[str] = []
        self.block_mode = "warn"  # off, warn or block new positions around corporate events
        self._lock = threading.Lock()
        doc = store.get("intelligence", "news_settings") or {}
        self.feeds = doc.get("feeds", [])
        self.block_mode = doc.get("block_mode", "warn")
        self.feed_errors: dict[str, str] = {}

    def configure(self, *, feeds: list[str] | None = None, block_mode: str | None = None) -> dict[str, Any]:
        if feeds is not None:
            bad = [f for f in feeds if not f.startswith(("https://", "http://"))]
            if bad:
                raise ValidationError("INVALID_FEED", [{"field": "feeds", "message": f"not a URL: {bad[0]}"}])
            self.feeds = list(dict.fromkeys(feeds))[:20]
        if block_mode is not None:
            if block_mode not in ("off", "warn", "block"):
                raise ValidationError(
                    "INVALID_MODE", [{"field": "block_mode", "message": "off, warn or block"}]
                )
            self.block_mode = block_mode
        self._store.put("intelligence", "news_settings", {"feeds": self.feeds, "block_mode": self.block_mode})
        return self.settings()

    def settings(self) -> dict[str, Any]:
        return {"feeds": self.feeds, "block_mode": self.block_mode, "feed_errors": self.feed_errors}

    # ---- news -----------------------------------------------------------------------------------------

    def match(self, text: str, explicit: list[str] | None = None) -> list[str]:
        """Instruments named in the text: by exact instrument id or by symbol as a whole word."""
        known = {i.instrument_id: i for i in self._instruments()}
        found = [i for i in explicit or [] if i in known]
        upper = f" {re.sub(r'[^A-Z0-9&]+', ' ', text.upper())} "
        for iid, instrument in known.items():
            symbol = instrument.base_asset.upper()
            if len(symbol) >= 3 and f" {symbol} " in upper and iid not in found:
                found.append(iid)
        return found[:10]

    def ingest(
        self,
        headline: str,
        *,
        source: str = "manual",
        url: str = "",
        summary: str = "",
        at: datetime | None = None,
        instruments: list[str] | None = None,
    ) -> NewsItem:
        headline = headline.strip()
        if not headline:
            raise ValidationError("INVALID_NEWS", [{"field": "headline", "message": "required"}])
        at = at or self._now()
        news_id = "N-" + hashlib.sha256(f"{source}|{url}|{headline}".encode()).hexdigest()[:14]
        existing = self._store.get(NEWS_KIND, news_id)
        if existing is not None:
            return _news(existing)
        item = NewsItem(
            news_id,
            at,
            headline[:500],
            source,
            url,
            summary[:2000],
            self.match(f"{headline} {summary}", instruments),
        )
        self._store.put(NEWS_KIND, news_id, item.to_dict())
        for iid in item.instruments:
            self._events.emit(
                MarketEvent(
                    iid,
                    "NEWS",
                    at,
                    headline[:200],
                    {"news_id": news_id, "url": url, "source": source},
                    source=source,
                    key=news_id,
                )
            )
        self._trim()
        return item

    def _trim(self) -> None:
        items = self._store.all(NEWS_KIND)
        if len(items) > MAX_NEWS:
            for old in sorted(items, key=lambda d: d["at"])[: len(items) - MAX_NEWS]:
                self._store.delete(NEWS_KIND, old["news_id"])

    def list(self, instrument_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        items = [
            d
            for d in self._store.all(NEWS_KIND)
            if instrument_id is None or instrument_id in d["instruments"]
        ]
        return sorted(items, key=lambda d: d["at"], reverse=True)[:limit]

    def poll(self) -> int:
        """Fetch the configured feeds; returns how many new items arrived."""
        added = 0
        for url in list(self.feeds):
            try:
                response = self._http.get(url, headers={"User-Agent": "JD Quant AI news reader"})
                response.raise_for_status()
                items = parse_feed(response.text)
                self.feed_errors.pop(url, None)
            except Exception as exc:
                self.feed_errors[url] = str(exc)[:200]
                continue
            for entry in items[:50]:
                before = self._store.get(
                    NEWS_KIND,
                    "N-"
                    + hashlib.sha256(f"{url}|{entry['link']}|{entry['title']}".encode()).hexdigest()[:14],
                )
                if before is not None:
                    continue
                self.ingest(
                    entry["title"],
                    source=url,
                    url=entry["link"],
                    summary=entry["summary"],
                    at=entry["at"] or self._now(),
                )
                added += 1
        return added

    def reaction(self, news_id: str) -> dict[str, Any]:
        """How each named instrument moved around the news, and what the engines saw meanwhile."""
        doc = self._store.get(NEWS_KIND, news_id)
        if doc is None:
            raise NotFoundError("NEWS_NOT_FOUND", f"unknown news item {news_id}")
        item = _news(doc)
        out = []
        for iid in item.instruments:
            bars = self._bars(iid)
            at = item.at

            def price_at(t, bars=bars):
                before = [b for b in bars if b.start <= t]
                return before[-1].close if before else None

            base = price_at(at)
            points = {}
            for label, delta in (("-5m", -5), ("0", 0), ("+1m", 1), ("+5m", 5), ("+15m", 15), ("+60m", 60)):
                t = at + timedelta(minutes=delta)
                if t > self._now():
                    continue
                p = price_at(t)
                points[label] = {"price": p, "change_pct": (p - base) / base * 100 if p and base else None}
            vol_before = sum(b.volume for b in bars if at - timedelta(minutes=15) <= b.start < at)
            vol_after = sum(b.volume for b in bars if at <= b.start < at + timedelta(minutes=15))
            events = self._event_store.search(
                instrument_id=iid, since=at - timedelta(minutes=5), until=at + timedelta(minutes=30), limit=50
            )
            out.append(
                {
                    "instrument_id": iid,
                    "prices": points,
                    "volume_15m_before": vol_before,
                    "volume_15m_after": vol_after,
                    "volume_ratio": vol_after / vol_before if vol_before else None,
                    "timeline": [e.to_dict() for e in sorted(events, key=lambda e: e.at)],
                }
            )
        return {"news": item.to_dict(), "reactions": out}

    # ---- corporate events -----------------------------------------------------------------------------

    def add_corporate(
        self, instrument_id: str, kind: str, day: date, details: str = "", source: str = "manual"
    ):
        known = {i.instrument_id for i in self._instruments()}
        problems = []
        if instrument_id not in known:
            problems.append({"field": "instrument_id", "message": f"unknown instrument {instrument_id}"})
        if kind not in CORPORATE_KINDS:
            problems.append({"field": "kind", "message": f"one of {', '.join(CORPORATE_KINDS)}"})
        if problems:
            raise ValidationError("INVALID_CORPORATE_EVENT", problems)
        event_id = "CE-" + hashlib.sha256(f"{instrument_id}|{kind}|{day}".encode()).hexdigest()[:12]
        event = CorporateEvent(event_id, instrument_id, kind, day, details[:500], source)
        self._store.put(CORP_KIND, event_id, event.to_dict())
        return event

    def import_csv(self, text: str) -> dict[str, Any]:
        """Rows of symbol or instrument id, date (YYYY-MM-DD), kind, details."""
        known = {i.instrument_id: i for i in self._instruments()}
        by_symbol = {i.base_asset.upper(): iid for iid, i in known.items()}
        added, errors = 0, []
        for n, row in enumerate(csv.reader(io.StringIO(text)), start=1):
            if not row or row[0].strip().lower() in ("symbol", "instrument", "instrument_id"):
                continue
            try:
                name, day, kind = row[0].strip(), date.fromisoformat(row[1].strip()), row[2].strip().upper()
                details = row[3].strip() if len(row) > 3 else ""
                iid = name if name in known else by_symbol.get(name.upper())
                if iid is None:
                    raise ValueError(f"unknown symbol {name}")
                self.add_corporate(iid, kind if kind in CORPORATE_KINDS else "OTHER", day, details, "csv")
                added += 1
            except (IndexError, ValueError, ValidationError) as exc:
                errors.append(f"line {n}: {exc}")
        return {"added": added, "errors": errors[:20]}

    def corporate(self, *, days: int = 30, instrument_id: str | None = None) -> list[dict[str, Any]]:
        today = self._now().date()
        items = [
            d
            for d in self._store.all(CORP_KIND)
            if today - timedelta(days=1) <= date.fromisoformat(d["day"]) <= today + timedelta(days=days)
            and (instrument_id is None or d["instrument_id"] == instrument_id)
        ]
        return sorted(items, key=lambda d: d["day"])

    def delete_corporate(self, event_id: str) -> None:
        if self._store.get(CORP_KIND, event_id) is None:
            raise NotFoundError("CORPORATE_EVENT_NOT_FOUND", f"unknown corporate event {event_id}")
        self._store.delete(CORP_KIND, event_id)

    def upcoming_for(self, instrument_id: str, within_days: int = 1) -> dict[str, Any] | None:
        """A corporate event for the instrument today or within the next day(s), if any."""
        found = self.corporate(days=within_days, instrument_id=instrument_id)
        today = self._now().date()
        return next((d for d in found if date.fromisoformat(d["day"]) >= today), None)

    def raise_due(self) -> None:
        """Emit today's corporate events once, so they appear in timelines and the event graph."""
        today = self._now().date()
        for d in self._store.all(CORP_KIND):
            if d["day"] != today.isoformat() or d.get("raised"):
                continue
            self._events.emit(
                MarketEvent(
                    d["instrument_id"],
                    "CORPORATE_EVENT",
                    self._now(),
                    f"{d['kind'].replace('_', ' ').title()} today"
                    + (f": {d['details']}" if d["details"] else ""),
                    {"corporate_event_id": d["event_id"], "kind": d["kind"]},
                    key=d["event_id"],
                )
            )
            d["raised"] = True
            self._store.put(CORP_KIND, d["event_id"], d)


def _news(doc: dict[str, Any]) -> NewsItem:
    return NewsItem(
        doc["news_id"],
        datetime.fromisoformat(doc["at"]),
        doc["headline"],
        doc["source"],
        doc.get("url", ""),
        doc.get("summary", ""),
        doc.get("instruments", []),
    )
