"""Whisper transcription: language handling and transcript contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from mura.asr.factory import ASRConfigurationError, build_asr_client
from mura.asr.language import read_languages
from mura.asr.whisper import (
    KAZAKH_ORTHOGRAPHY_PROMPT,
    WhisperASRClient,
    model_reports_timings,
)
from mura.config import ASRProvider


class _Response:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self) -> object:
        return self._payload


class _Session:
    """Captures the outgoing request so the contract can be asserted on it."""

    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self.response


def _client(response: _Response) -> tuple[WhisperASRClient, _Session]:
    session = _Session(response)
    client = WhisperASRClient(
        api_key="k", base_url="https://api.example.com/v1", model="whisper-1", session=session
    )
    return client, session


MIXED = "Менің әжем Алматыда тұрды, потом мы поехали к ней летом"


def _payload(text: str = MIXED, language: str = "kk") -> dict[str, object]:
    return {
        "text": text,
        "language": language,
        "duration": 6.5,
        "segments": [
            {"id": 0, "start": 0.0, "end": 3.0, "text": "Менің әжем Алматыда тұрды,"},
            {"id": 1, "start": 3.0, "end": 6.5, "text": "потом мы поехали к ней летом"},
        ],
    }


def test_language_is_never_pinned_and_translation_is_never_requested(tmp_path: Path) -> None:
    audio = tmp_path / "a.webm"
    audio.write_bytes(b"x")
    client, session = _client(_Response(_payload()))

    client.transcribe(audio_path=audio, recording_id="rec_1")

    sent = session.calls[0]
    assert sent["url"].endswith("/audio/transcriptions")
    assert "translations" not in str(sent["url"])
    # The decisive assertion: forcing a language mangles code-switched speech.
    assert "language" not in sent["data"]


def test_code_switched_speech_is_reported_as_mixed(tmp_path: Path) -> None:
    audio = tmp_path / "a.webm"
    audio.write_bytes(b"x")
    client, _ = _client(_Response(_payload()))

    envelope = client.transcribe(audio_path=audio, recording_id="rec_1")

    assert envelope.asr_metadata["detected_language"] == "mixed"
    assert envelope.asr_metadata["mixed_language"] is True
    assert envelope.asr_metadata["transcript_languages"] == "kk,ru"
    assert envelope.language_hints == ["kk", "ru"]


def test_transcript_is_preserved_verbatim(tmp_path: Path) -> None:
    audio = tmp_path / "a.webm"
    audio.write_bytes(b"x")
    client, _ = _client(_Response(_payload()))

    envelope = client.transcribe(audio_path=audio, recording_id="rec_1")

    # Kazakh stays Kazakh and Russian stays Russian; nothing is normalised.
    assert envelope.full_text == MIXED
    assert len(envelope.segments) == 2
    assert envelope.segments[0].text.startswith("Менің")


def test_whisper_own_language_claim_is_kept_beside_the_reading(tmp_path: Path) -> None:
    audio = tmp_path / "a.webm"
    audio.write_bytes(b"x")
    client, _ = _client(_Response(_payload(language="ru")))

    envelope = client.transcribe(audio_path=audio, recording_id="rec_1")

    # Whisper said "ru"; the text shows both. The disagreement stays visible.
    assert envelope.asr_metadata["asr_reported_language"] == "ru"
    assert envelope.asr_metadata["detected_language"] == "mixed"


def test_empty_transcript_is_a_failure_not_an_empty_success(tmp_path: Path) -> None:
    audio = tmp_path / "a.webm"
    audio.write_bytes(b"x")
    client, _ = _client(_Response({"text": "   ", "language": "ru", "duration": 1.0}))

    with pytest.raises(Exception) as excinfo:
        client.transcribe(audio_path=audio, recording_id="rec_1")
    assert "empty transcript" in str(excinfo.value)


def test_missing_segments_still_produce_a_valid_envelope(tmp_path: Path) -> None:
    audio = tmp_path / "a.webm"
    audio.write_bytes(b"x")
    client, _ = _client(_Response({"text": MIXED, "language": "kk", "duration": 4.0}))

    envelope = client.transcribe(audio_path=audio, recording_id="rec_1")

    assert len(envelope.segments) == 1
    assert envelope.segments[0].text == MIXED


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (MIXED, "mixed"),
        ("Моя бабушка жила в Алматы, затем мы поехали к ней летом", "ru"),
        ("Менің әжем Алматыда тұрды", "kk"),
        ("Ол бізге келді", "kk"),
        ("", "unknown"),
    ],
)
def test_language_reading(text: str, expected: str) -> None:
    assert read_languages(text).detected == expected


@pytest.mark.parametrize(
    "text",
    [
        "Да, мы поехали к ней летом",
        "Те дни я помню",
        "Та встреча была давно",
    ],
)
def test_russian_particles_are_not_read_as_kazakh(text: str) -> None:
    """Unambiguous Russian must never be reported as code-switched.

    «да», «де», «та» and «те» are Kazakh clitics and also everyday Russian
    words. Treating them as Kazakh evidence claimed a language that was never
    spoken, which is the one thing language reporting must not do.
    """

    reading = read_languages(text)
    assert reading.detected == "ru"
    assert reading.mixed is False


class _WhisperSettings:
    asr_provider = ASRProvider.WHISPER
    asr_request_timeout_seconds = 900.0
    whisper_base_url = "https://api.example.com/v1"
    whisper_model = "whisper-1"
    whisper_api_key: str | None = None


def test_whisper_without_a_key_fails_at_construction() -> None:
    with pytest.raises(ASRConfigurationError):
        build_asr_client(_WhisperSettings())


def test_whisper_client_does_not_wait_for_a_registered_worker() -> None:
    settings = _WhisperSettings()
    settings.whisper_api_key = "k"
    client = build_asr_client(settings)
    assert client.requires_registered_worker is False


class TestDeclaredLanguage:
    """A language the speaker chose is honoured; one they did not is not invented."""

    @staticmethod
    def _sent(tmp_path: Path, declared: str | None) -> dict[str, object]:
        audio = tmp_path / "a.webm"
        audio.write_bytes(b"x")
        client, session = _client(_Response(_payload()))
        client.transcribe(
            audio_path=audio, recording_id="rec_1", declared_language=declared
        )
        return session.calls[0]["data"]

    def test_auto_still_sends_no_language(self, tmp_path: Path) -> None:
        # The case the whole design exists for: an unpinned decoder is the only
        # way a code-switched sentence survives.
        assert "language" not in self._sent(tmp_path, None)
        assert "language" not in self._sent(tmp_path, "auto")
        assert "language" not in self._sent(tmp_path, "mixed")

    def test_declared_kazakh_is_passed_with_an_orthography_hint(
        self, tmp_path: Path
    ) -> None:
        sent = self._sent(tmp_path, "kk")
        assert sent["language"] == "kk"
        # Without the hint Whisper flattens ә ғ қ ң ө ұ ү һ і onto Russian
        # letters, which quietly misspells every name in the archive.
        assert sent["prompt"] == KAZAKH_ORTHOGRAPHY_PROMPT

    def test_declared_russian_is_passed_without_a_kazakh_hint(
        self, tmp_path: Path
    ) -> None:
        sent = self._sent(tmp_path, "ru")
        assert sent["language"] == "ru"
        assert "prompt" not in sent

    def test_an_unknown_declaration_is_ignored_rather_than_forwarded(
        self, tmp_path: Path
    ) -> None:
        # A value the recogniser would reject must not reach it and fail the job.
        assert "language" not in self._sent(tmp_path, "tr")


def test_the_kazakh_hint_actually_carries_kazakh_letters() -> None:
    """A hint spelled in Russian would teach the decoder the wrong thing."""

    assert set("әғқңөұүһі") & set(KAZAKH_ORTHOGRAPHY_PROMPT)


class TestModelsWithoutTimestamps:
    """gpt-4o transcription is far better at Kazakh but reports text only."""

    @staticmethod
    def _client(model: str) -> tuple[WhisperASRClient, _Session]:
        session = _Session(_Response({"text": "Бірінші сөйлем. Екінші сөйлем."}))
        return (
            WhisperASRClient(
                api_key="k",
                base_url="https://api.example.com/v1",
                model=model,
                session=session,
            ),
            session,
        )

    def test_capability_is_decided_by_model(self) -> None:
        assert model_reports_timings("whisper-1") is True
        assert model_reports_timings("gpt-4o-transcribe") is False
        assert model_reports_timings("gpt-4o-mini-transcribe") is False

    def test_verbose_json_is_never_requested_from_a_model_that_rejects_it(
        self, tmp_path: Path
    ) -> None:
        # Asking gpt-4o for verbose_json is a hard 400, so every recording
        # would fail rather than degrade.
        audio = tmp_path / "a.webm"
        audio.write_bytes(b"x")
        client, session = self._client("gpt-4o-transcribe")

        client.transcribe(audio_path=audio, recording_id="rec_1")

        data = session.calls[0]["data"]
        assert data["response_format"] == "json"
        assert "timestamp_granularities[]" not in data

    def test_provenance_stays_per_sentence(self, tmp_path: Path) -> None:
        # Collapsing to one segment would make every claim in the archive cite
        # the same id, which destroys the granularity evidence depends on.
        audio = tmp_path / "a.webm"
        audio.write_bytes(b"x")
        client, _ = self._client("gpt-4o-transcribe")

        envelope = client.transcribe(audio_path=audio, recording_id="rec_1")

        assert len(envelope.segments) == 2
        assert envelope.segments[0].text == "Бірінші сөйлем."
        assert envelope.segments[1].text == "Екінші сөйлем."

    def test_estimated_timings_are_labelled_as_estimated(self, tmp_path: Path) -> None:
        # The split is exact; the times are a guess. Nothing downstream may
        # mistake a guess for something the recogniser measured.
        audio = tmp_path / "a.webm"
        audio.write_bytes(b"x")
        client, _ = self._client("gpt-4o-transcribe")

        envelope = client.transcribe(audio_path=audio, recording_id="rec_1")

        assert envelope.asr_metadata["timings_estimated"] is True

    def test_reported_timings_are_not_labelled_as_estimated(
        self, tmp_path: Path
    ) -> None:
        audio = tmp_path / "a.webm"
        audio.write_bytes(b"x")
        client, _ = _client(_Response(_payload()))

        envelope = client.transcribe(audio_path=audio, recording_id="rec_1")

        assert envelope.asr_metadata["timings_estimated"] is False

    def test_language_is_still_read_from_the_text(self, tmp_path: Path) -> None:
        # gpt-4o reports no language field at all, so the deterministic reader
        # is the only thing standing between the archive and "unknown".
        audio = tmp_path / "a.webm"
        audio.write_bytes(b"x")
        client, _ = self._client("gpt-4o-transcribe")

        envelope = client.transcribe(audio_path=audio, recording_id="rec_1")

        assert envelope.asr_metadata["detected_language"] == "kk"


def test_a_retryable_failure_stays_catchable_through_a_context_manager() -> None:
    """A recoverable ASR failure must not become a worker crash.

    `ASRClientError` was a frozen dataclass. Python writes `__traceback__` onto
    an exception as it propagates, and a frozen dataclass refuses that write —
    so every retryable failure raised `FrozenInstanceError` from inside the
    handler that would have deferred the job. The worker died, supervision
    restarted it, and it claimed the same recording again: a provider being
    briefly slow became an unbounded crash loop.
    """

    from contextlib import contextmanager

    from mura.asr.client import ASRClientError

    @contextmanager
    def materialised():
        yield

    with pytest.raises(ASRClientError) as raised:
        with materialised():
            raise ASRClientError("provider timed out", retryable=True)

    assert raised.value.retryable is True
    assert raised.value.__traceback__ is not None
