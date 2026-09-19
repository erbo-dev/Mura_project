"""Whisper transcription over an OpenAI-compatible HTTP API.

Two rules shape this client, and both come from what MURA records.

**Never translate.** `/audio/translations` always returns English. Only
`/audio/transcriptions` returns what was actually said, so this client calls
that endpoint and no other. A family archive that silently rendered a
grandmother's Kazakh into English would be destroying the record it exists to
keep.

**Never pin a language.** Whisper accepts an optional `language` parameter that
forces its decoder. MURA recordings routinely switch between Kazakh and Russian
inside one sentence, so forcing either mangles the other. The parameter is
deliberately never sent, and the UI language never reaches this module: what
the interface is set to says nothing about what was spoken into the microphone.

Whisper reports a single language per request. That is recorded as it was
given, and `mura.asr.language` separately reads the returned text to report
every language actually present.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import requests

from mura.asr.client import ASRClientError
from mura.asr.language import read_languages
from mura.domain.models import RawSegment, TranscriptEnvelope

#: Anything below this is silence or a stray tap, not a memory.
MIN_DURATION_SECONDS = 0.05

#: Seeds Whisper's decoder with correct Kazakh orthography.
#:
#: Kazakh is a low-resource language for Whisper and it drifts toward Russian
#: spelling, flattening the nine letters that only Kazakh has (ә ғ қ ң ө ұ ү һ і)
#: onto their nearest Russian neighbours. A prompt is a decoding hint, not
#: training: it biases spelling without inventing content, and the vocabulary
#: chosen here is what these recordings are actually full of — family words and
#: place names.
KAZAKH_ORTHOGRAPHY_PROMPT = (
    "Менің әжем мен атам Алматыда тұрды. Отбасы, ауыл, немере, қыз, ұл, "
    "туған күн, соғыс жылдары, Қазақстан, Шымкент, Қарағанды."
)

#: What each declared language means to the recogniser.
LANGUAGE_CODES = {"ru": "ru", "kk": "kk"}

#: Typical Cyrillic speech rate, used only to scale estimated segment bounds.
#: Never presented as a measurement — see `timings_estimated`.
CHARS_PER_SECOND = 14.0

#: Sentence-ish boundaries. Splitting on them keeps one segment per sentence so
#: provenance stays granular when the recogniser reports no segments of its own.
_SENTENCE = re.compile(r"[^.!?…]+[.!?…]*\s*")


def model_reports_timings(model: str) -> bool:
    """Whether this model can return per-segment timestamps.

    The gpt-4o transcription models are markedly better at Kazakh but accept
    only `json`, so they report text and nothing else. Asking them for
    `verbose_json` is a hard 400, not a soft degrade.
    """

    return not model.startswith("gpt-4o")


class WhisperASRClient:
    """Transcribe audio with a hosted Whisper deployment.

    Works against any OpenAI-compatible transcription endpoint, so the same
    client serves OpenAI and the Groq/Together style hosts without a branch.
    """

    #: Whisper is reached directly over HTTPS. Unlike the tunnelled GPU worker
    #: there is no registration handshake, so the orchestrator must not defer
    #: jobs waiting for a worker row that will never appear.
    requires_registered_worker = False

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 900,
        session: requests.Session | None = None,
        on_usage: Any | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.on_usage = on_usage

    def transcribe(
        self,
        *,
        audio_path: Path,
        recording_id: str,
        content_type: str | None = None,
        worker_url: str | None = None,
        declared_language: str | None = None,
    ) -> TranscriptEnvelope:
        """Transcribe, optionally honouring a language the speaker declared.

        `declared_language` is only ever "ru" or "kk", and only when the speaker
        chose it themselves. Auto and mixed pass nothing, so code-switched speech
        still reaches an unpinned decoder — the case the whole design protects.

        Honouring an explicit choice is not the same as forcing one. A speaker
        who says "this is Kazakh" is giving the recogniser information it
        otherwise has to guess, and guessing is exactly where Kazakh loses.
        """

        del worker_url  # Hosted API; the orchestrator passes None.

        timed = model_reports_timings(self.model)
        request: dict[str, str] = {"model": self.model}
        if timed:
            # Segment timestamps, where the model can report them.
            request["response_format"] = "verbose_json"
            request["timestamp_granularities[]"] = "segment"
        else:
            request["response_format"] = "json"
        language = LANGUAGE_CODES.get(declared_language or "")
        if language:
            request["language"] = language
        if language == "kk":
            request["prompt"] = KAZAKH_ORTHOGRAPHY_PROMPT

        started = time.perf_counter()
        try:
            with audio_path.open("rb") as audio_file:
                response = self.session.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    data=request,
                    files={
                        "file": (
                            audio_path.name,
                            audio_file,
                            content_type or "application/octet-stream",
                        )
                    },
                    # Connect generously: this request carries the whole
                    # audio file, and a 20s budget timed out mid-upload on
                    # Railway's egress before the recogniser saw a byte.
                    timeout=(60, self.timeout_seconds),
                )
        except (OSError, requests.RequestException) as exc:
            elapsed = time.perf_counter() - started
            if self.on_usage is not None:
                try:
                    self.on_usage(
                        provider="whisper",
                        model=self.model,
                        operation="transcription",
                        latency_ms=int(elapsed * 1000),
                        success=False,
                        audio_seconds=None,
                        error_code="provider_timeout" if isinstance(exc, requests.Timeout) else "transcription_failed",
                    )
                except Exception:
                    pass
            raise ASRClientError(f"Whisper is unreachable: {exc}", retryable=True) from exc

        if response.status_code >= 400:
            elapsed = time.perf_counter() - started
            if self.on_usage is not None:
                try:
                    err_code = "provider_rate_limit" if response.status_code == 429 else (
                        "provider_auth_error" if response.status_code in (401, 403) else "transcription_failed"
                    )
                    self.on_usage(
                        provider="whisper",
                        model=self.model,
                        operation="transcription",
                        latency_ms=int(elapsed * 1000),
                        success=False,
                        audio_seconds=None,
                        error_code=err_code,
                    )
                except Exception:
                    pass
            raise ASRClientError(
                f"Whisper returned HTTP {response.status_code}",
                retryable=response.status_code in {408, 409, 425, 429}
                or response.status_code >= 500,
                status_code=response.status_code,
            )

        try:
            payload: Any = response.json()
        except ValueError as exc:
            elapsed = time.perf_counter() - started
            if self.on_usage is not None:
                try:
                    self.on_usage(
                        provider="whisper",
                        model=self.model,
                        operation="transcription",
                        latency_ms=int(elapsed * 1000),
                        success=False,
                        audio_seconds=None,
                        error_code="invalid_provider_response",
                    )
                except Exception:
                    pass
            raise ASRClientError("Whisper returned a non-JSON body", retryable=False) from exc

        return self._envelope(payload, recording_id=recording_id)
        envelope = self._envelope(payload, recording_id=recording_id)
        elapsed = time.perf_counter() - started
        if self.on_usage is not None:
            try:
                self.on_usage(
                    provider="whisper",
                    model=self.model,
                    operation="transcription",
                    latency_ms=int(elapsed * 1000),
                    success=True,
                    audio_seconds=envelope.duration_seconds,
                    error_code=None,
                )
            except Exception:
                pass
        return envelope

    def _envelope(self, payload: Any, *, recording_id: str) -> TranscriptEnvelope:
        if not isinstance(payload, dict):
            raise ASRClientError("Whisper returned an unexpected body", retryable=False)

        full_text = str(payload.get("text") or "").strip()
        if not full_text:
            # An empty transcript is a failed job, never an empty success: a
            # memory that silently became nothing is worse than a visible error.
            raise ASRClientError("Whisper returned an empty transcript", retryable=False)

        reported = payload.get("segments")
        if isinstance(reported, list) and reported:
            segments = self._segments(reported, full_text=full_text)
            estimated = False
        else:
            # The model returned text only. Sentences are a real property of
            # that text, so provenance stays per-sentence; the bounds are a
            # scaled guess and are labelled as one rather than passed off as
            # measurements.
            segments = self._estimated_segments(full_text)
            estimated = True

        duration = float(payload.get("duration") or 0.0) or max(s.end for s in segments)

        reading = read_languages(full_text)
        metadata: dict[str, str | int | float | bool] = dict(reading.as_metadata)
        metadata["timings_estimated"] = estimated
        reported = payload.get("language")
        if isinstance(reported, str) and reported:
            # What Whisper itself claimed, kept distinct from what the text
            # shows, so a disagreement stays visible instead of being resolved
            # silently in favour of either one.
            metadata["asr_reported_language"] = reported

        return TranscriptEnvelope(
            recording_id=recording_id,
            duration_seconds=max(duration, MIN_DURATION_SECONDS),
            language_hints=list(reading.languages),
            full_text=full_text,
            segments=segments,
            asr_model=self.model,
            asr_revision=str(payload.get("model") or self.model),
            chunker_version="whisper-segments-1",
            asr_metadata=metadata,
        )

    @staticmethod
    def _estimated_segments(full_text: str) -> list[RawSegment]:
        """One segment per sentence, with bounds scaled from text length.

        Used only for models that report no timings at all. The split itself is
        exact — sentence boundaries are in the text — while the times are a
        uniform-rate estimate, which `timings_estimated` marks so nothing
        downstream mistakes them for something the recogniser measured.
        """

        sentences = [match.group(0).strip() for match in _SENTENCE.finditer(full_text)]
        sentences = [sentence for sentence in sentences if sentence]
        if not sentences:
            sentences = [full_text]

        segments: list[RawSegment] = []
        cursor = 0.0
        for index, sentence in enumerate(sentences):
            span = max(len(sentence) / CHARS_PER_SECOND, MIN_DURATION_SECONDS)
            segments.append(
                RawSegment(
                    segment_id=f"seg_{index:04d}",
                    start=cursor,
                    end=cursor + span,
                    text=sentence,
                )
            )
            cursor += span
        return segments

    @staticmethod
    def _segments(raw: Any, *, full_text: str) -> list[RawSegment]:
        """Whisper's segments, or one covering segment when it returns none."""

        segments: list[RawSegment] = []
        if isinstance(raw, list):
            for index, item in enumerate(raw):
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                start = float(item.get("start") or 0.0)
                end = float(item.get("end") or 0.0)
                if end <= start:
                    end = start + MIN_DURATION_SECONDS
                segments.append(
                    RawSegment(segment_id=f"seg_{index:04d}", start=start, end=end, text=text)
                )

        if not segments:
            segments.append(
                RawSegment(
                    segment_id="seg_0000", start=0.0, end=MIN_DURATION_SECONDS, text=full_text
                )
            )
        return segments
