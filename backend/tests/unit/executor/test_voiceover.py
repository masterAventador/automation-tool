"""PC-07: synthesising one beat's narration and finding out how long it is.

Why the duration has to be measured rather than assumed
------------------------------------------------------
The film's shot lengths are `max(narration, animation)` (P0-1d), so the timeline
cannot be laid out until the narration's real length is known — and the gateway
does not return one. Measured 2026-07-27 against the real service:

    10 characters -> 2.160 s     35 -> 6.080 s     72 -> 14.000 s

That is close enough to linear (~0.19 s per character) to be a useful budget to
hand the model up front, and nowhere near exact enough to lay a timeline on.

Why the URL is not simply fetched
---------------------------------
The gateway answers with a signed, expiring object-store link, and it hands it
over as **plain `http://`** — measured, not assumed. The same host serves the
same object over TLS (also measured), so the scheme is upgraded before the
fetch rather than the audio being pulled in the clear. Anything that is not an
http(s) URL on a declared host is refused outright: this is the one place in
the authoring path that fetches a URL a remote service chose.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import cast

import pytest

from automation_tool.executor.motion_authoring.agent import (
    AuthoringWorkspace,
    MotionAuthoringRejected,
)
from automation_tool.executor.motion_authoring.voiceover import (
    MAX_VOICEOVER_BYTES,
    VoiceoverConfig,
    VoiceoverRejected,
    load_voiceover_config,
    resolve_audio_url,
    synthesize_voiceover,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CATALOG_PATH = REPOSITORY_ROOT / "contracts/video/bailian-model-catalog.v1.json"

_KEY = "sk-" + "a" * 40


def _config() -> VoiceoverConfig:
    return VoiceoverConfig(
        base_url="https://dashscope.aliyuncs.com/api/v1",
        model_id="qwen3-tts-instruct-flash-2026-01-26",
        api_key=_KEY,
        voice="Cherry",
        audio_host_suffixes=(".aliyuncs.com",),
    )


def _workspace(tmp_path: Path) -> AuthoringWorkspace:
    return AuthoringWorkspace(tmp_path)


class _Gateway:
    """A stand-in for the service, so these tests never reach the network."""

    def __init__(
        self,
        url: str = "http://x.oss-cn-beijing.aliyuncs.com/a.wav?sig=1",
        audio: bytes = b"RIFF....WAVE",
    ) -> None:
        self.url = url
        self.audio = audio
        self.fetched: list[str] = []

    def post(self, url: str, body: bytes, headers: dict[str, str], timeout: int) -> bytes:
        assert headers["Authorization"] == f"Bearer {_KEY}"
        return json.dumps({"output": {"audio": {"url": self.url}}}).encode("utf-8")

    def get(self, url: str, timeout: int) -> bytes:
        self.fetched.append(url)
        return self.audio


def test_the_catalog_config_builds_from_a_key_the_request_carried() -> None:
    """PC-26：执行器子进程里没有 secret 文件——apiKey 随授权书请求到达，
    配置只能从（打包的目录契约 + 请求里的 key）建。与 load_voiceover_config
    共用同一段目录解析，不允许第二份实现。"""
    from pathlib import Path

    from automation_tool.executor.motion_authoring.voiceover import (
        voiceover_config_from_catalog,
    )

    catalog = Path(__file__).resolve().parents[4] / "contracts/video/bailian-model-catalog.v1.json"
    config = voiceover_config_from_catalog(catalog_path=catalog, api_key="sk-" + "a" * 40)
    assert config.base_url.startswith("https://")
    assert config.model_id
    assert config.audio_host_suffixes


def test_the_api_key_never_reaches_a_representation() -> None:
    assert _KEY not in repr(_config())


@pytest.mark.parametrize(
    "base_url", ["http://dashscope.aliyuncs.com/api/v1", "dashscope.aliyuncs.com"]
)
def test_the_gateway_must_be_https(base_url: str) -> None:
    with pytest.raises(VoiceoverRejected):
        VoiceoverConfig(
            base_url=base_url,
            model_id="m",
            api_key=_KEY,
            voice="Cherry",
            audio_host_suffixes=(".aliyuncs.com",),
        )


def test_a_plain_http_audio_url_is_upgraded_rather_than_fetched_in_the_clear() -> None:
    resolved = resolve_audio_url(
        "http://dashscope-a717.oss-cn-beijing.aliyuncs.com/a.wav?Expires=1",
        allowed_suffixes=(".aliyuncs.com",),
    )
    assert resolved.startswith("https://")
    assert resolved.endswith("/a.wav?Expires=1")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://dashscope.aliyuncs.com/a.wav",
        "data:audio/wav;base64,AAAA",
        "https://evil.example.com/a.wav",
        "https://aliyuncs.com.evil.example/a.wav",
        "//dashscope.aliyuncs.com/a.wav",
        "",
    ],
)
def test_an_audio_url_outside_the_declared_hosts_is_refused(url: str) -> None:
    with pytest.raises(VoiceoverRejected):
        resolve_audio_url(url, allowed_suffixes=(".aliyuncs.com",))


def test_synthesis_writes_the_audio_inside_the_workspace(tmp_path: Path) -> None:
    gateway = _Gateway()
    result = synthesize_voiceover(
        _config(),
        "智能客服，全天在线。",
        workspace=_workspace(tmp_path),
        relative_path="audio/beat-1.wav",
        post=gateway.post,
        fetch=gateway.get,
    )
    written = tmp_path / "audio/beat-1.wav"
    assert written.is_file()
    assert written.read_bytes() == gateway.audio
    assert result.relative_path == "audio/beat-1.wav"
    assert result.bytes_written == len(gateway.audio)
    # Upgraded before the fetch, not after.
    assert gateway.fetched == ["https://x.oss-cn-beijing.aliyuncs.com/a.wav?sig=1"]


def test_a_path_escaping_the_workspace_is_refused(tmp_path: Path) -> None:
    with pytest.raises(MotionAuthoringRejected):
        synthesize_voiceover(
            _config(),
            "x",
            workspace=_workspace(tmp_path),
            relative_path="../escape.wav",
            post=_Gateway().post,
            fetch=_Gateway().get,
        )


def test_audio_larger_than_the_budget_is_refused(tmp_path: Path) -> None:
    gateway = _Gateway(audio=b"\x00" * (MAX_VOICEOVER_BYTES + 1))
    with pytest.raises(VoiceoverRejected):
        synthesize_voiceover(
            _config(),
            "x",
            workspace=_workspace(tmp_path),
            relative_path="audio/beat-1.wav",
            post=gateway.post,
            fetch=gateway.get,
        )


def test_empty_narration_is_refused_before_any_call(tmp_path: Path) -> None:
    gateway = _Gateway()
    with pytest.raises(VoiceoverRejected):
        synthesize_voiceover(
            _config(),
            "   ",
            workspace=_workspace(tmp_path),
            relative_path="audio/beat-1.wav",
            post=gateway.post,
            fetch=gateway.get,
        )
    assert gateway.fetched == []


@pytest.mark.parametrize(
    "answer",
    [
        {},
        {"output": {}},
        {"output": {"audio": {}}},
        {"output": {"audio": {"url": 1}}},
    ],
)
def test_an_answer_without_a_usable_audio_url_is_refused(
    tmp_path: Path, answer: dict[str, object]
) -> None:
    def post(url: str, body: bytes, headers: dict[str, str], timeout: int) -> bytes:
        return json.dumps(answer).encode("utf-8")

    with pytest.raises(VoiceoverRejected):
        synthesize_voiceover(
            _config(),
            "x",
            workspace=_workspace(tmp_path),
            relative_path="audio/beat-1.wav",
            post=post,
            fetch=_Gateway().get,
        )


def test_the_catalog_declares_a_voiceover_purpose_with_its_own_endpoint() -> None:
    """TTS is not on the OpenAI-compatible endpoint — measured, it answers 404.

    So the purpose carries its own `base_url` and `api_mode` rather than
    inheriting the catalog's, and the hosts its audio may come from are
    declared beside them instead of being a literal in the code.
    """
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    purpose = next(entry for entry in catalog["purposes"] if entry["id"] == "voiceover")
    assert purpose["api_mode"] == "dashscope_native"
    assert purpose["base_url"] == "https://dashscope.aliyuncs.com/api/v1"
    assert purpose["default_model_id"] in purpose["allowed_model_ids"]
    assert purpose["audio_host_suffixes"] == [".aliyuncs.com"]


def test_the_config_is_built_from_the_catalog_and_the_secret(tmp_path: Path) -> None:
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"apiKey": _KEY}), encoding="utf-8")
    config = load_voiceover_config(catalog_path=CATALOG_PATH, secret_path=secret)
    assert config is not None
    assert config.base_url == "https://dashscope.aliyuncs.com/api/v1"
    assert config.audio_host_suffixes == (".aliyuncs.com",)


def test_no_secret_means_no_voiceover_rather_than_a_hidden_default(tmp_path: Path) -> None:
    assert (
        load_voiceover_config(catalog_path=CATALOG_PATH, secret_path=tmp_path / "absent.json")
        is None
    )


def test_an_audio_url_with_no_host_is_refused() -> None:
    """Scheme-only URLs parse fine and name nothing to connect to."""
    with pytest.raises(VoiceoverRejected):
        resolve_audio_url("https:///a.wav", allowed_suffixes=(".aliyuncs.com",))


def test_the_shipped_transport_maps_urlopen_failures_to_bounded_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reason must never carry the key or the upstream body."""
    from automation_tool.executor.motion_authoring import voiceover as module

    class _Reason(OSError):
        def __init__(self) -> None:
            super().__init__("connection reset by peer")
            self.reason = TimeoutError("timed out")

    for label, failure, expected in [
        ("a bare timeout", TimeoutError("timed out"), "timed out"),
        ("a timeout behind a reason", _Reason(), "timed out"),
        ("any other socket failure", OSError("connection reset"), "transport failed"),
    ]:

        def refusing_urlopen(
            *_args: object, _failure: BaseException = failure, **_kw: object
        ) -> object:
            raise _failure

        monkeypatch.setattr(urllib.request, "urlopen", refusing_urlopen)
        with pytest.raises(VoiceoverRejected) as caught:
            module._post_json("https://example.invalid/x", b"{}", {}, 5)
        assert expected in str(caught.value), label


def test_a_download_that_fails_is_a_bounded_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    from automation_tool.executor.motion_authoring import voiceover as module

    def refusing_urlopen(*_args: object, **_kw: object) -> bytes:
        raise OSError("connection reset")

    monkeypatch.setattr(urllib.request, "urlopen", refusing_urlopen)

    with pytest.raises(VoiceoverRejected) as caught:
        module._get_bytes("https://example.invalid/a.wav", 5)

    assert "download failed" in str(caught.value)


def test_synthesis_refuses_a_config_or_workspace_of_the_wrong_type(tmp_path: Path) -> None:
    gateway = _Gateway()

    with pytest.raises(VoiceoverRejected):
        synthesize_voiceover(
            cast(VoiceoverConfig, object()),
            "x",
            workspace=_workspace(tmp_path),
            relative_path="audio/beat-1.wav",
            post=gateway.post,
            fetch=gateway.get,
        )

    with pytest.raises(VoiceoverRejected):
        synthesize_voiceover(
            _config(),
            "x",
            workspace=cast(AuthoringWorkspace, object()),
            relative_path="audio/beat-1.wav",
            post=gateway.post,
            fetch=gateway.get,
        )


def test_an_answer_that_is_not_a_json_object_is_refused(tmp_path: Path) -> None:
    """Exit-zero nonsense is a shape gateways produce; it is not read around."""
    for label, raw in [
        ("not json at all", b"<html>error</html>"),
        ("json that is not an object", b"[1, 2]"),
    ]:
        root = tmp_path / label.replace(" ", "-")
        root.mkdir()

        def answering_post(
            _url: str, _body: bytes, _headers: dict[str, str], _timeout: int, _raw: bytes = raw
        ) -> bytes:
            return _raw

        with pytest.raises(VoiceoverRejected):
            synthesize_voiceover(
                _config(),
                "x",
                workspace=_workspace(root),
                relative_path="audio/beat-1.wav",
                post=answering_post,
                fetch=_Gateway().get,
            )
        assert label


def test_a_catalog_or_secret_that_cannot_be_read_is_refused(tmp_path: Path) -> None:
    secret = tmp_path / "secret.json"
    secret.write_text("{not json", encoding="utf-8")

    with pytest.raises(VoiceoverRejected):
        load_voiceover_config(catalog_path=CATALOG_PATH, secret_path=secret)

    good_secret = tmp_path / "good.json"
    good_secret.write_text(json.dumps({"apiKey": _KEY}), encoding="utf-8")
    broken_catalog = tmp_path / "catalog.json"
    broken_catalog.write_text("{not json", encoding="utf-8")

    with pytest.raises(VoiceoverRejected):
        load_voiceover_config(catalog_path=broken_catalog, secret_path=good_secret)


def test_a_secret_or_catalog_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    secret = tmp_path / "secret.json"
    secret.write_text("[1, 2]", encoding="utf-8")

    with pytest.raises(VoiceoverRejected):
        load_voiceover_config(catalog_path=CATALOG_PATH, secret_path=secret)

    good_secret = tmp_path / "good.json"
    good_secret.write_text(json.dumps({"apiKey": _KEY}), encoding="utf-8")
    list_catalog = tmp_path / "catalog.json"
    list_catalog.write_text("[1, 2]", encoding="utf-8")

    with pytest.raises(VoiceoverRejected):
        load_voiceover_config(catalog_path=list_catalog, secret_path=good_secret)


def test_a_secret_without_a_key_means_no_voiceover(tmp_path: Path) -> None:
    """Present but keyless is the same answer as absent: the feature is off."""
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"note": "no key here"}), encoding="utf-8")

    assert load_voiceover_config(catalog_path=CATALOG_PATH, secret_path=secret) is None


def test_a_catalog_missing_its_voiceover_purpose_is_refused(tmp_path: Path) -> None:
    from automation_tool.executor.motion_authoring.voiceover import (
        voiceover_config_from_catalog,
    )

    for label, document in [
        ("no purposes list", {"purposes": {}}),
        ("no voiceover purpose", {"purposes": [{"id": "vision"}]}),
        (
            "an api mode that drifted",
            {"purposes": [{"id": "voiceover", "api_mode": "openai_compatible"}]},
        ),
        (
            "no declared audio hosts",
            {"purposes": [{"id": "voiceover", "api_mode": "dashscope_native"}]},
        ),
        (
            "an audio host that is not text",
            {
                "purposes": [
                    {
                        "id": "voiceover",
                        "api_mode": "dashscope_native",
                        "audio_host_suffixes": [1],
                    }
                ]
            },
        ),
    ]:
        catalog = tmp_path / f"catalog-{abs(hash(label))}.json"
        catalog.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(VoiceoverRejected):
            voiceover_config_from_catalog(catalog_path=catalog, api_key=_KEY)
        assert label


def test_measuring_audio_asks_the_toolchain_and_refuses_what_it_cannot_use(
    tmp_path: Path,
) -> None:
    from automation_tool.executor.motion_authoring.voiceover import measure_audio_seconds

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF....WAVE")
    linked = tmp_path / "linked.wav"
    linked.symlink_to(audio)

    def stub(name: str, body: str) -> Path:
        path = tmp_path / name
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    ok = stub("ok", 'echo \'{"format":{"duration":"1.5"}}\'')
    assert measure_audio_seconds(audio, ffprobe=ok) == 1.5

    for label, path, ffprobe in [
        ("a directory", tmp_path, ok),
        ("a symlink", linked, ok),
        ("nothing there", tmp_path / "absent.wav", ok),
        ("a probe that refuses", audio, stub("bad", "exit 1")),
        ("a probe answering nonsense", audio, stub("junk", "echo 'not json'")),
        ("a probe answering no duration", audio, stub("empty", "echo '{\"format\":{}}'")),
        (
            "a duration of zero",
            audio,
            stub("zero", 'echo \'{"format":{"duration":"0"}}\''),
        ),
    ]:
        with pytest.raises(VoiceoverRejected):
            measure_audio_seconds(path, ffprobe=ffprobe)
        assert label


class _Response:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_arguments: object) -> None:
        return None

    def read(self, _size: int = -1) -> bytes:
        return self._payload


def test_the_shipped_transport_returns_what_the_gateway_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from automation_tool.executor.motion_authoring import voiceover as module

    def answering_urlopen(*_args: object, **_kw: object) -> _Response:
        return _Response(b'{"output":{}}')

    monkeypatch.setattr(urllib.request, "urlopen", answering_urlopen)

    assert module._post_json("https://example.invalid/x", b"{}", {}, 5) == b'{"output":{}}'
    assert module._get_bytes("https://example.invalid/a.wav", 5) == b'{"output":{}}'


def test_the_catalog_only_parser_refuses_what_it_cannot_read(tmp_path: Path) -> None:
    """The authoring child has no secret file, so this entry parses on its own."""
    from automation_tool.executor.motion_authoring.voiceover import (
        voiceover_config_from_catalog,
    )

    malformed = tmp_path / "broken.json"
    malformed.write_text("{not json", encoding="utf-8")
    not_an_object = tmp_path / "list.json"
    not_an_object.write_text("[1, 2]", encoding="utf-8")

    for label, path in [
        ("no file at all", tmp_path / "absent.json"),
        ("a document that will not parse", malformed),
        ("a document that is not an object", not_an_object),
    ]:
        with pytest.raises(VoiceoverRejected):
            voiceover_config_from_catalog(catalog_path=path, api_key=_KEY)
        assert label
