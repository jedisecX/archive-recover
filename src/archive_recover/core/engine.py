"""Recovery orchestration: DISCOVER → INDEX → QUEUE → RETRIEVE → VERIFY → STORE.

All state lives in SQLite, so every stage is resumable:
- discovery checkpoints the source's resume key after each page
- each retrieved capture is committed individually; re-running ``recover``
  continues with whatever is still pending (or failed under the attempt cap)
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from archive_recover import __version__
from archive_recover.adapters import ADAPTER_CLASSES
from archive_recover.adapters.base import BaseAdapter, CaptureRecord
from archive_recover.core.config import Config, mime_matches
from archive_recover.core.db import (
    URL,
    Capture,
    Domain,
    ErrorRecord,
    JobCheckpoint,
    RecoveryJob,
    Resource,
    init_db,
    make_session_factory,
)
from archive_recover.core.http import FetchError, HttpSettings
from archive_recover.core.utils import (
    domain_from_url,
    normalize_url,
    safe_join,
    sha1_base32,
    sha256_bytes,
    url_in_scope,
    utcnow,
)

Log = Callable[[str], None]
RETRIABLE = {"network", "server", "rate_limited", "challenge"}


@dataclass
class SourceDiscovery:
    source: str
    pages: int = 0
    seen: int = 0
    new: int = 0
    out_of_scope: int = 0
    complete: bool = False
    error: Optional[str] = None
    notes: Optional[str] = None
    resumed_from: Optional[str] = None


@dataclass
class RecoverSummary:
    attempted: int = 0
    recovered: int = 0
    deduplicated: int = 0
    failed: int = 0
    skipped_storage: int = 0
    digest_mismatch: int = 0
    bytes_written: int = 0
    paused_sources: list[str] = field(default_factory=list)
    errors: Counter = field(default_factory=Counter)


def build_adapter(name: str, cfg: Config, transport: Any = None) -> BaseAdapter:
    if name not in ADAPTER_CLASSES:
        raise KeyError(f"unknown source {name!r}; valid: {', '.join(ADAPTER_CLASSES)}")
    settings = HttpSettings(
        user_agent=cfg.user_agent,
        timeout=cfg.timeout,
        rate_limit_delay=cfg.rate_limit_delay,
        max_retries=cfg.max_retries,
        max_object_bytes=cfg.max_object_bytes,
        transport=transport,
    )
    kwargs: dict[str, Any] = {"settings": settings}
    if name == "wayback" and cfg.collapse:
        kwargs["collapse"] = cfg.collapse
    if name == "memento" and cfg.timemaps:
        kwargs["timemaps"] = cfg.timemaps
    return ADAPTER_CLASSES[name](**kwargs)


class RecoveryEngine:
    def __init__(
        self,
        cfg: Config,
        *,
        adapters: Optional[list[BaseAdapter]] = None,
        transport: Any = None,
        log: Log = print,
    ) -> None:
        if not cfg.domain:
            raise ValueError("domain is required")
        self.cfg = cfg
        self.domain_name = domain_from_url(cfg.domain)
        if not self.domain_name:
            raise ValueError(f"invalid domain: {cfg.domain!r}")
        self.log = log
        self.engine = init_db(cfg.db_path)
        self.Session = make_session_factory(self.engine)
        self.root = Path(cfg.output_dir) / self.domain_name
        self.root.mkdir(parents=True, exist_ok=True)
        self._transport = transport
        self._adapters: dict[str, BaseAdapter] = {a.name: a for a in (adapters or [])}
        with self.Session() as s:
            self.domain_id = self._domain(s).id
            s.commit()

    # ------------------------------------------------------------------ helpers
    def adapter(self, name: str) -> BaseAdapter:
        if name not in self._adapters:
            self._adapters[name] = build_adapter(name, self.cfg, self._transport)
        return self._adapters[name]

    async def close(self) -> None:
        for a in self._adapters.values():
            await a.close()

    def _domain(self, s: Session) -> Domain:
        d = s.scalar(select(Domain).where(Domain.name == self.domain_name))
        if d is None:
            d = Domain(name=self.domain_name, normalized=self.domain_name)
            s.add(d)
            s.flush()
        return d

    def _start_job(self, s: Session, kind: str, extra: dict[str, Any]) -> RecoveryJob:
        job = RecoveryJob(
            domain_id=self.domain_id, kind=kind, status="running", started_at=utcnow(),
            config={"version": __version__, **extra},
        )
        s.add(job)
        s.commit()
        return job

    def _error(self, s: Session, job_id: Optional[int], category: str, message: str,
               capture_id: Optional[int] = None) -> None:
        s.add(ErrorRecord(job_id=job_id, capture_id=capture_id, category=category,
                          message=message[:4000]))

    def _last_resume_key(self, s: Session, source: str) -> Optional[str]:
        """Resume key of the newest unfinished discover checkpoint for this source."""
        row = s.execute(
            select(JobCheckpoint, RecoveryJob)
            .join(RecoveryJob, JobCheckpoint.job_id == RecoveryJob.id)
            .where(RecoveryJob.domain_id == self.domain_id,
                   JobCheckpoint.stage == f"discover:{source}")
            .order_by(JobCheckpoint.id.desc())
            .limit(1)
        ).first()
        if not row:
            return None
        cp, _job = row
        if (cp.data or {}).get("complete"):
            return None
        return cp.resume_key

    # ------------------------------------------------------------------ discover
    def _persist(self, s: Session, records: list[CaptureRecord], url_cache: dict[str, int],
                 enforce_scope: bool) -> tuple[int, int]:
        new = out_of_scope = 0
        for r in records:
            seed_snapshot = r.extra.get("original_unknown")
            if enforce_scope and not seed_snapshot and not url_in_scope(
                r.original_url, self.domain_name, self.cfg.include_subdomains
            ):
                out_of_scope += 1
                continue
            norm = normalize_url(r.original_url)
            url_id = url_cache.get(norm)
            if url_id is None:
                s.execute(
                    sqlite_insert(URL)
                    .values(domain_id=self.domain_id, original=r.original_url, normalized=norm,
                            first_seen=utcnow())
                    .on_conflict_do_nothing()
                )
                url_id = s.scalar(select(URL.id).where(URL.domain_id == self.domain_id,
                                                       URL.normalized == norm))
                url_cache[norm] = url_id
            status = "pending" if r.snapshot_url else "metadata_only"
            res = s.execute(
                sqlite_insert(Capture)
                .values(
                    url_id=url_id, source=r.source, timestamp=r.timestamp,
                    timestamp_str=r.timestamp_str, snapshot_url=r.snapshot_url,
                    mime_type=(r.mime_type or None), status_code=r.status_code,
                    digest=r.digest, length=r.length, recovery_status=status,
                    extra=r.extra or None, attempts=0, discovered_at=utcnow(),
                    capture_key=Capture.make_key(r.source, r.original_url, r.timestamp_str,
                                                 r.snapshot_url),
                )
                .on_conflict_do_nothing(index_elements=["capture_key"])
            )
            new += res.rowcount or 0
        return new, out_of_scope

    async def discover(self, sources: Optional[list[str]] = None, *, resume: bool = True,
                       seeds: Optional[list[str]] = None) -> list[SourceDiscovery]:
        sources = sources or self.cfg.sources
        seeds = seeds if seeds is not None else self.cfg.seeds
        results = []
        for name in sources:
            try:
                results.append(await self._discover_source(name, resume=resume, seeds=seeds))
            except Exception as exc:  # isolation: one source never blocks another
                results.append(SourceDiscovery(source=name, error=f"{type(exc).__name__}: {exc}"))
        return results

    async def _discover_source(self, name: str, *, resume: bool, seeds: list[str]) -> SourceDiscovery:
        adapter = self.adapter(name)
        out = SourceDiscovery(source=name)
        if adapter.needs_seeds() and not seeds:
            out.complete = True
            out.notes = "skipped: source needs seed URLs (--seeds FILE)"
            return out
        with self.Session() as s:
            job = self._start_job(s, "discover", {"source": name, "seeds": len(seeds)})
            key = self._last_resume_key(s, name) if resume else None
            out.resumed_from = key
            url_cache: dict[str, int] = {}
            enforce_scope = adapter.supports_domain_enumeration()
            while True:
                res = await adapter.discover(
                    self.domain_name, since=self.cfg.since, until=self.cfg.until,
                    seed_urls=seeds or None, resume_key=key, limit=self.cfg.page_size,
                    include_subdomains=self.cfg.include_subdomains,
                )
                out.pages += 1
                out.seen += len(res.captures)
                out.notes = res.source_notes
                new, oos = self._persist(s, res.captures, url_cache, enforce_scope)
                out.new += new
                out.out_of_scope += oos
                if res.error:
                    out.error = res.error
                    self._error(s, job.id, "discover", f"{name}: {res.error}")
                next_key = res.next_resume_key
                s.add(JobCheckpoint(
                    job_id=job.id, stage=f"discover:{name}", resume_key=next_key or key,
                    data={"page": out.pages, "seen": out.seen, "new": out.new,
                          "complete": bool(res.complete and not res.error)},
                ))
                s.commit()
                self.log(f"  {name}: page {out.pages} +{new} new ({out.seen} seen)")
                if res.error or res.complete or not next_key:
                    out.complete = bool(res.complete and not res.error)
                    break
                if next_key == key:
                    out.error = "resume key did not advance; stopping to avoid a loop"
                    break
                key = next_key
                if self.cfg.max_pages and out.pages >= self.cfg.max_pages:
                    out.notes = (out.notes or "") + f" (stopped at max_pages={self.cfg.max_pages})"
                    break
            job.status = "completed" if out.complete else ("failed" if out.error else "partial")
            job.completed_at = utcnow()
            self._domain(s).last_checked = utcnow()
            s.commit()
        return out

    # ------------------------------------------------------------------ recover
    def _pending_query(self, sources: list[str], retry_failed: bool):
        statuses = ["pending"] + (["failed"] if retry_failed else [])
        return (
            select(Capture)
            .join(URL, Capture.url_id == URL.id)
            .where(URL.domain_id == self.domain_id,
                   Capture.source.in_(sources),
                   Capture.recovery_status.in_(statuses),
                   Capture.snapshot_url.is_not(None),
                   or_(Capture.recovery_status == "pending",
                       Capture.attempts < self.cfg.max_attempts))
            .order_by(Capture.timestamp.is_(None), Capture.timestamp, Capture.id)
        )

    def _storage_used(self, s: Session) -> int:
        sub = (select(Resource.content_hash, func.max(Resource.size).label("size"))
               .join(Capture, Resource.capture_id == Capture.id)
               .join(URL, Capture.url_id == URL.id)
               .where(URL.domain_id == self.domain_id)
               .group_by(Resource.content_hash).subquery())
        return int(s.scalar(select(func.coalesce(func.sum(sub.c.size), 0))) or 0)

    def _write_object(self, data: bytes, digest: str) -> str:
        rel = f"objects/{digest[:2]}/{digest}"
        path = safe_join(self.root, rel)
        if path.exists() and path.stat().st_size == len(data):
            return rel
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)  # atomic
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return rel

    async def recover(self, sources: Optional[list[str]] = None, *, limit: Optional[int] = None,
                      retry_failed: bool = True, types: Optional[list[str]] = None) -> RecoverSummary:
        sources = sources or self.cfg.sources
        types = types if types is not None else self.cfg.retrieve_types
        summary = RecoverSummary()
        if self.cfg.metadata_only:
            self.log("metadata_only is set: nothing retrieved.")
            return summary

        with self.Session() as s:
            job = self._start_job(s, "recover", {"sources": sources, "types": types, "limit": limit})
            job_id = job.id
            candidates = [c for c in s.scalars(self._pending_query(sources, retry_failed))
                          if mime_matches(c.mime_type, types)]
            used = self._storage_used(s)
        if limit:
            candidates = candidates[:limit]
        self.log(f"  {len(candidates)} captures queued ({used} bytes already stored)")

        queue: asyncio.Queue[Capture] = asyncio.Queue()
        for c in candidates:
            queue.put_nowait(c)
        strikes: Counter = Counter()
        paused: set[str] = set()
        storage = {"used": used}
        lock = asyncio.Lock()

        async def worker() -> None:
            while True:
                try:
                    cap = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    if cap.source in paused:
                        continue
                    await self._recover_one(cap, job_id, summary, strikes, paused, storage, lock)
                finally:
                    queue.task_done()

        status = "completed"
        try:
            await asyncio.gather(*(worker() for _ in range(self.cfg.max_concurrency)))
        except (asyncio.CancelledError, KeyboardInterrupt):
            status = "interrupted"
            raise
        finally:
            with self.Session() as s:
                j = s.get(RecoveryJob, job_id)
                j.status = status if not paused else "partial"
                j.completed_at = utcnow()
                s.add(JobCheckpoint(job_id=job_id, stage="recover", data={
                    "attempted": summary.attempted, "recovered": summary.recovered,
                    "deduplicated": summary.deduplicated, "failed": summary.failed,
                    "paused_sources": sorted(paused)}))
                s.commit()
        summary.paused_sources = sorted(paused)
        return summary

    async def _recover_one(self, cap: Capture, job_id: int, summary: RecoverSummary,
                           strikes: Counter, paused: set[str], storage: dict, lock) -> None:
        summary.attempted += 1
        # Content-addressed dedupe via source digest (IA SHA-1): skip the download entirely.
        if cap.digest:
            with self.Session() as s:
                twin = s.execute(
                    select(Resource).join(Capture, Resource.capture_id == Capture.id)
                    .where(Capture.source == cap.source, Capture.digest == cap.digest,
                           Resource.sha1_b32 == cap.digest, Resource.local_path.is_not(None))
                    .limit(1)
                ).scalar_one_or_none()
                if twin and safe_join(self.root, twin.local_path).exists():
                    s.add(Resource(capture_id=cap.id, content_hash=twin.content_hash,
                                   local_path=twin.local_path, size=twin.size, verified=True,
                                   sha1_b32=twin.sha1_b32, http_status=twin.http_status,
                                   content_type=twin.content_type, final_url=twin.final_url,
                                   verification_notes=f"dedup: identical digest to capture "
                                                      f"{twin.capture_id}"))
                    s.execute(update(Capture).where(Capture.id == cap.id).values(
                        recovery_status="recovered", last_error=None))
                    s.commit()
                    summary.recovered += 1
                    summary.deduplicated += 1
                    return

        adapter = self.adapter(cap.source)
        rec = CaptureRecord(original_url="", timestamp=cap.timestamp,
                            timestamp_str=cap.timestamp_str, snapshot_url=cap.snapshot_url,
                            mime_type=cap.mime_type, status_code=cap.status_code,
                            digest=cap.digest, length=cap.length, source=cap.source)
        try:
            content, meta = await adapter.retrieve(rec)
        except (FetchError, ValueError) as exc:
            category = getattr(exc, "category", "invalid")
            summary.failed += 1
            summary.errors[category] += 1
            permanent = category not in RETRIABLE
            with self.Session() as s:
                s.execute(update(Capture).where(Capture.id == cap.id).values(
                    recovery_status="failed",
                    attempts=Capture.attempts + (self.cfg.max_attempts if permanent else 1),
                    last_error=f"{category}: {exc}"[:2000]))
                self._error(s, job_id, category, str(exc), capture_id=cap.id)
                s.commit()
            if category in ("rate_limited", "challenge"):
                strikes[cap.source] += 1
                if strikes[cap.source] >= self.cfg.circuit_breaker and cap.source not in paused:
                    paused.add(cap.source)
                    self.log(f"  ! pausing {cap.source}: {strikes[cap.source]} consecutive "
                             f"{category} responses. Re-run later to continue.")
            return
        strikes[cap.source] = 0

        h = sha256_bytes(content)
        sha1 = sha1_base32(content)
        async with lock:
            with self.Session() as s:
                already = s.scalar(select(func.count()).select_from(Resource)
                                   .where(Resource.content_hash == h)) or 0
                if not already and storage["used"] + len(content) > self.cfg.max_storage_bytes:
                    summary.skipped_storage += 1
                    s.execute(update(Capture).where(Capture.id == cap.id).values(
                        last_error="storage limit reached"))
                    s.commit()
                    return
                rel = self._write_object(content, h)
                if not already:
                    storage["used"] += len(content)
                    summary.bytes_written += len(content)
                notes, verified = [], False
                if cap.digest and cap.source in ("wayback", "memento"):
                    if cap.digest.upper() == sha1:
                        verified = True
                        notes.append("source digest (SHA-1) matches payload")
                    else:
                        summary.digest_mismatch += 1
                        notes.append(f"source digest {cap.digest} != payload {sha1} "
                                     "(archive may have transformed or re-encoded the payload)")
                else:
                    notes.append("no source digest; SHA-256 recorded at retrieval")
                s.add(Resource(
                    capture_id=cap.id, content_hash=h, local_path=rel, size=len(content),
                    verified=verified, verification_notes="; ".join(notes), sha1_b32=sha1,
                    http_status=meta.get("status_code"),
                    content_type=(meta.get("content_type") or "")[:255] or None,
                    final_url=meta.get("final_url"), retrieved_at=utcnow(),
                ))
                values: dict[str, Any] = {"recovery_status": "recovered", "last_error": None,
                                          "attempts": Capture.attempts + 1}
                if not cap.mime_type and meta.get("content_type"):
                    values["mime_type"] = meta["content_type"].split(";")[0].strip()[:128]
                s.execute(update(Capture).where(Capture.id == cap.id).values(**values))
                s.commit()
        summary.recovered += 1

    # ------------------------------------------------------------------ verify / reconcile
    def verify(self, *, fix: bool = False) -> dict[str, int]:
        """Re-hash every stored object. With ``fix``, broken captures go back to pending."""
        out = Counter()
        with self.Session() as s:
            rows = s.execute(
                select(Resource, Capture).join(Capture, Resource.capture_id == Capture.id)
                .join(URL, Capture.url_id == URL.id).where(URL.domain_id == self.domain_id)
            ).all()
            hashed: dict[str, Optional[str]] = {}
            for res, cap in rows:
                out["checked"] += 1
                try:
                    path = safe_join(self.root, res.local_path or "")
                except ValueError:
                    path = None
                if path is None or not path.is_file():
                    problem = "missing"
                else:
                    if res.local_path not in hashed:
                        hashed[res.local_path] = sha256_bytes(path.read_bytes())
                    problem = None if hashed[res.local_path] == res.content_hash else "corrupt"
                if problem is None:
                    out["ok"] += 1
                    continue
                out[problem] += 1
                self.log(f"  {problem}: capture {cap.id} {res.local_path}")
                if fix:
                    s.delete(res)
                    cap.recovery_status = "pending"
                    cap.last_error = f"verify: {problem} on disk"
                    out["requeued"] += 1
            s.commit()
        return dict(out)

    def reconcile(self) -> dict[str, int]:
        """Disk consistency (requeue missing/corrupt) + reset retriable failures."""
        result = self.verify(fix=True)
        with self.Session() as s:
            ids = select(Capture.id).join(URL, Capture.url_id == URL.id).where(
                URL.domain_id == self.domain_id, Capture.recovery_status == "failed",
                Capture.attempts < self.cfg.max_attempts)
            n = s.execute(update(Capture).where(Capture.id.in_(ids))
                          .values(recovery_status="pending")).rowcount
            s.commit()
        result["failed_requeued"] = n or 0
        return result

    # ------------------------------------------------------------------ status / report
    def stats(self) -> dict[str, Any]:
        with self.Session() as s:
            base = (select(Capture.source, Capture.recovery_status, func.count())
                    .join(URL, Capture.url_id == URL.id)
                    .where(URL.domain_id == self.domain_id)
                    .group_by(Capture.source, Capture.recovery_status))
            by_source: dict[str, dict[str, int]] = defaultdict(dict)
            for src, st, n in s.execute(base):
                by_source[src][st] = n
            urls = s.scalar(select(func.count()).select_from(URL)
                            .where(URL.domain_id == self.domain_id)) or 0
            urls_recovered = s.scalar(
                select(func.count(func.distinct(Capture.url_id))).join(URL, Capture.url_id == URL.id)
                .where(URL.domain_id == self.domain_id, Capture.recovery_status == "recovered")) or 0
            mimes = Counter()
            for mime, st, n in s.execute(
                select(Capture.mime_type, Capture.recovery_status, func.count())
                .join(URL, Capture.url_id == URL.id).where(URL.domain_id == self.domain_id)
                .group_by(Capture.mime_type, Capture.recovery_status)
            ):
                mimes[(mime or "unknown").split(";")[0]] += n
            verified = s.scalar(
                select(func.count()).select_from(Resource).join(Capture).join(URL)
                .where(URL.domain_id == self.domain_id, Resource.verified.is_(True))) or 0
            errors = dict(s.execute(
                select(ErrorRecord.category, func.count())
                .join(RecoveryJob, ErrorRecord.job_id == RecoveryJob.id)
                .where(RecoveryJob.domain_id == self.domain_id)
                .group_by(ErrorRecord.category)).all())
            jobs = [
                {"id": j.id, "kind": j.kind, "status": j.status,
                 "source": (j.config or {}).get("source"),
                 "started": j.started_at.isoformat() if j.started_at else None,
                 "completed": j.completed_at.isoformat() if j.completed_at else None}
                for j in s.scalars(select(RecoveryJob).where(RecoveryJob.domain_id == self.domain_id)
                                   .order_by(RecoveryJob.id.desc()).limit(10))
            ]
            # Latest discover outcome per source
            discovery: dict[str, str] = {}
            for j in s.scalars(select(RecoveryJob).where(
                    RecoveryJob.domain_id == self.domain_id, RecoveryJob.kind == "discover")
                    .order_by(RecoveryJob.id)):
                discovery[(j.config or {}).get("source", "?")] = j.status
            stored = self._storage_used(s)
            first, last = s.execute(
                select(func.min(Capture.timestamp), func.max(Capture.timestamp))
                .join(URL, Capture.url_id == URL.id).where(URL.domain_id == self.domain_id)).one()
            unique_objects = s.scalar(
                select(func.count(func.distinct(Resource.content_hash))).join(Capture).join(URL)
                .where(URL.domain_id == self.domain_id)) or 0
        totals = Counter()
        for st in by_source.values():
            totals.update(st)
        return {
            "domain": self.domain_name, "unique_urls": urls, "urls_with_recovered_capture": urls_recovered,
            "captures_total": sum(totals.values()), "captures_by_status": dict(totals),
            "captures_by_source": {k: dict(v) for k, v in by_source.items()},
            "top_mime_types": dict(mimes.most_common(12)), "verified_by_source_digest": verified,
            "bytes_stored": stored, "unique_objects": unique_objects,
            "earliest_capture": first.isoformat() if first else None,
            "latest_capture": last.isoformat() if last else None,
            "errors_by_category": errors, "recent_jobs": jobs,
            "discovery_status_by_source": discovery,
        }

    def report(self) -> tuple[Path, Path, dict[str, Any]]:
        st = self.stats()
        enum_sources = [n for n, cls in ADAPTER_CLASSES.items()
                        if cls().supports_domain_enumeration()]
        done = [s for s, v in st["discovery_status_by_source"].items()
                if v == "completed" and s in enum_sources]
        st["discovery_complete_within_scope"] = done
        st["generated_at"] = utcnow().isoformat() + "Z"
        st["tool_version"] = __version__
        jpath = self.root / "report.json"
        mpath = self.root / "report.md"
        jpath.write_text(json.dumps(st, indent=2, default=str), encoding="utf-8")
        mpath.write_text(render_markdown(st, self.cfg), encoding="utf-8")
        return jpath, mpath, st


def render_markdown(st: dict[str, Any], cfg: Config) -> str:
    by = st["captures_by_status"]
    lines = [
        f"# Recovery report — {st['domain']}",
        "",
        f"Generated {st['generated_at']} by archive-recover {st['tool_version']}.",
        "",
        "## Scope",
        f"- Sources: {', '.join(cfg.sources)}",
        f"- Subdomains included: {'yes' if cfg.include_subdomains else 'no'}",
        f"- Date range: {cfg.since or 'any'} → {cfg.until or 'any'}",
        "",
        "## Coverage",
        f"- Unique URLs discovered: {st['unique_urls']}",
        f"- URLs with at least one recovered capture: {st['urls_with_recovered_capture']}",
        f"- Captures discovered: {st['captures_total']}",
        f"- Recovered: {by.get('recovered', 0)} · Pending: {by.get('pending', 0)} · "
        f"Failed: {by.get('failed', 0)} · Metadata-only: {by.get('metadata_only', 0)}",
        f"- Payloads verified against source digest: {st['verified_by_source_digest']}",
        f"- Unique content objects (SHA-256): {st['unique_objects']} · "
        f"Bytes stored: {st['bytes_stored']:,}",
        f"- Capture span: {st['earliest_capture'] or '—'} → {st['latest_capture'] or '—'}",
        "",
        "## By source",
        "",
        "| Source | Discovery | Recovered | Pending | Failed | Metadata-only |",
        "|---|---|---|---|---|---|",
    ]
    for src, counts in sorted(st["captures_by_source"].items()):
        lines.append(
            f"| {src} | {st['discovery_status_by_source'].get(src, '—')} | "
            f"{counts.get('recovered', 0)} | {counts.get('pending', 0)} | "
            f"{counts.get('failed', 0)} | {counts.get('metadata_only', 0)} |"
        )
    if st["errors_by_category"]:
        lines += ["", "## Errors", ""]
        lines += [f"- {k}: {v}" for k, v in sorted(st["errors_by_category"].items())]
    lines += ["", "## Completeness statement", ""]
    if st["discovery_complete_within_scope"]:
        lines.append(
            f"Discovery complete within the configured scope for: "
            f"{', '.join(st['discovery_complete_within_scope'])} (the source's enumeration "
            "interface terminated normally). This does not mean every historical version of "
            "the site was archived."
        )
    else:
        lines.append("Discovery is not known to be complete for any enumerating source.")
    lines.append(
        "Seed-based sources (archive.is, Megalodon, Memento) only cover the URLs supplied as seeds."
    )
    return "\n".join(lines) + "\n"
