import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from assister.voice.endpoint import EndpointConfig, Endpointer
from assister.voice.listener import MicListener
from assister.voice.sentences import SentenceSplitter, speakable
from assister.voice.stt import usable_text
from assister.voice.tts import Speaker, TtsError, Voice, decode_mp3
from assister.voice.wake import Heard, VoiceRouter, WakeMatch, find_wake, spoken_answer

# ---------- 문장 나누기 ----------


def split_all(*pieces: str) -> list[str]:
    splitter = SentenceSplitter()
    sentences = [sentence for piece in pieces for sentence in splitter.feed(piece)]
    return sentences + splitter.flush()


def test_sentences_come_out_as_soon_as_they_end():
    splitter = SentenceSplitter()

    assert splitter.feed("안녕하세요! 오늘은") == ["안녕하세요!"]
    assert splitter.feed(" 날씨가 좋네요. ") == ["오늘은 날씨가 좋네요."]
    assert splitter.flush() == []


def test_result_does_not_depend_on_how_text_is_chopped():
    text = "좋은 아침이에요! 3.5초면 돼요. 그럼 시작할까요?"

    assert split_all(text) == split_all(*text) == ["좋은 아침이에요!", "3.5초면 돼요.", "그럼 시작할까요?"]


def test_tiny_sentences_are_joined_to_the_next():
    assert split_all("네! 알겠어요. 바로 할게요.") == ["네! 알겠어요.", "바로 할게요."]


def test_line_breaks_end_a_sentence_and_leftover_is_flushed():
    assert split_all("남은 할 일이에요\n우유 사기\n메일 답장") == ["남은 할 일이에요", "우유 사기", "메일 답장"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("좋아요 😊", "좋아요"),
        ("**중요한** 내용이에요", "중요한 내용이에요"),
        ("# 제목\n- 항목", "제목 항목"),
        ("😊✨", ""),
        ("  여러   칸  ", "여러 칸"),
    ],
)
def test_speakable_drops_what_a_voice_cannot_read(text, expected):
    assert speakable(text) == expected


# ---------- 말의 시작과 끝 ----------

CONFIG = EndpointConfig()
FRAME = CONFIG.frame_samples


def silence(ms: int) -> list[np.ndarray]:
    rng = np.random.default_rng(0)
    return [rng.normal(0, 0.001, FRAME).astype(np.float32) for _ in range(ms // CONFIG.frame_ms)]


def tone(ms: int, amplitude: float = 0.2) -> list[np.ndarray]:
    t = np.arange(FRAME) / CONFIG.sample_rate
    frame = (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    return [frame.copy() for _ in range(ms // CONFIG.frame_ms)]


def run(endpointer: Endpointer, frames: list[np.ndarray]) -> list[np.ndarray]:
    return [utterance for frame in frames if (utterance := endpointer.feed(frame)) is not None]


def test_silence_alone_produces_nothing():
    assert run(Endpointer(CONFIG), silence(3000)) == []


def test_speech_followed_by_silence_becomes_one_utterance():
    endpointer = Endpointer(CONFIG)

    utterances = run(endpointer, silence(600) + tone(900) + silence(1200))

    assert len(utterances) == 1
    seconds = len(utterances[0]) / CONFIG.sample_rate
    # 말 0.9초 + 앞 여유 + 끝을 판단하느라 기다린 침묵
    assert 0.9 < seconds < 0.9 + (CONFIG.pre_roll_ms + CONFIG.end_silence_ms) / 1000 + 0.1
    assert not endpointer.in_speech


def test_in_speech_is_true_while_talking():
    endpointer = Endpointer(CONFIG)

    run(endpointer, silence(300) + tone(300))

    assert endpointer.in_speech


def test_short_click_is_ignored():
    assert run(Endpointer(CONFIG), silence(300) + tone(60) + silence(1500)) == []


def test_two_remarks_with_a_pause_become_two_utterances():
    assert len(run(Endpointer(CONFIG), silence(300) + tone(600) + silence(1000) + tone(600) + silence(1000))) == 2


def test_endless_sound_is_cut_at_the_limit():
    utterances = run(Endpointer(CONFIG), silence(300) + tone(CONFIG.max_speech_ms + 3000))

    assert len(utterances[0]) / CONFIG.sample_rate == pytest.approx(CONFIG.max_speech_ms / 1000, abs=0.5)


def test_steady_background_noise_stops_triggering():
    """선풍기 같은 일정한 소음은 한 번 걸러진 뒤로는 말로 치지 않는다."""
    endpointer = Endpointer(CONFIG)

    run(endpointer, tone(CONFIG.max_speech_ms + 1000, amplitude=0.05))

    assert run(endpointer, tone(20000, amplitude=0.05)) == []
    # 소음보다 훨씬 큰 말소리는 여전히 잡는다.
    assert len(run(endpointer, tone(600, amplitude=0.5) + tone(1200, amplitude=0.05))) == 1


# ---------- 이름 부르기 ----------


@pytest.mark.parametrize(
    ("text", "command"),
    [
        ("모모야", ""),
        ("모모야!", ""),
        ("모모야, 10분 뒤에 알람 맞춰 줘.", "10분 뒤에 알람 맞춰 줘."),
        ("모모 오늘 날씨 어때", "오늘 날씨 어때"),
        ("모 모야 불 꺼 줘", "불 꺼 줘"),
        ("어 모모야 있잖아", "있잖아"),
        ("모모님 안녕하세요", "안녕하세요"),
    ],
)
def test_calling_the_name_is_recognised(text, command):
    assert find_wake(text, ["모모"]) == WakeMatch(command)


@pytest.mark.parametrize(
    "text",
    ["", "오늘 날씨 좋다", "모모랑 어제 얘기했어", "모모는 귀여워", "어제 회의에서 들었는데 모모야 라는 말이 나왔어"],
)
def test_talking_about_the_name_is_not_a_call(text):
    assert find_wake(text, ["모모"]) is None


def test_other_spellings_can_be_registered():
    assert find_wake("뭐뭐야 불 켜 줘", ["모모", "뭐뭐"]) == WakeMatch("불 켜 줘")


@pytest.mark.parametrize(
    ("said", "answer"),
    [
        ("허락", True),
        ("응 해 줘", True),
        ("실행해", True),
        ("안 돼", False),
        ("아니 하지 마", False),
        ("실행하지 마", False),  # 거절하는 말이 섞였으면 거절이다
        ("취소", False),
        ("네", None),  # 조용한 소리를 '네'로 잘못 받아쓰는 일이 있어서 한 마디 답은 받지 않는다
        ("음 글쎄", None),
    ],
)
def test_spoken_answer_to_a_permission_question(said, answer):
    assert spoken_answer(said) is answer


def test_router_ignores_talk_that_is_not_addressed_to_the_character():
    router = VoiceRouter(["모모"], wake_enabled=True)

    assert router.on_transcript("오늘 저녁 뭐 먹지") is None


def test_router_passes_command_said_with_the_name():
    router = VoiceRouter(["모모"], wake_enabled=True)

    assert router.on_transcript("모모야 불 켜 줘") == Heard("불 켜 줘", "wake")
    assert not router.armed


def test_bare_name_arms_the_router_for_the_next_remark():
    router = VoiceRouter(["모모"], wake_enabled=True)

    assert router.on_transcript("모모야") == Heard("", "wake_only")
    assert router.armed
    assert router.on_transcript("") is None  # 잡음은 무시하고 계속 기다린다
    assert router.on_transcript("불 켜 줘") == Heard("불 켜 줘", "command")
    assert not router.armed


def test_armed_router_takes_any_remark_even_with_wake_word_off():
    router = VoiceRouter(["모모"], wake_enabled=False)
    assert router.on_transcript("모모야 불 켜 줘") is None

    router.arm()

    assert router.on_transcript("모모야 불 켜 줘") == Heard("불 켜 줘", "command")


# ---------- 받아쓰기 결과 거르기 ----------


def segment(text: str, no_speech_prob: float = 0.05, avg_logprob: float = -0.3):
    return SimpleNamespace(text=text, no_speech_prob=no_speech_prob, avg_logprob=avg_logprob)


def test_usable_text_joins_confident_segments():
    assert usable_text([segment(" 모모야,"), segment(" 불 켜 줘.")]) == "모모야, 불 켜 줘."


def test_usable_text_drops_silence_and_known_hallucinations():
    assert usable_text([segment("아아", no_speech_prob=0.9)]) == ""
    assert usable_text([segment("웅얼", avg_logprob=-1.8)]) == ""
    assert usable_text([segment("시청해주셔서 감사합니다.")]) == ""
    assert usable_text([segment("구독과 좋아요 부탁드립니다")]) == ""
    assert usable_text([segment(" 아멘.")]) == ""
    assert usable_text([segment("아멘이라고 말했어")]) == "아멘이라고 말했어"


# ---------- 목소리 내기 ----------


def encode_mp3(samples: np.ndarray, sample_rate: int) -> bytes:
    import io

    import av

    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="mp3") as container:
        stream = container.add_stream("mp3", rate=sample_rate)
        frame = av.AudioFrame.from_ndarray((samples * 32767).astype(np.int16).reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = sample_rate
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()


def test_decode_mp3_gives_mono_float_audio_at_the_requested_rate():
    one_second = (0.3 * np.sin(2 * np.pi * 440 * np.arange(24000) / 24000)).astype(np.float32)

    decoded = decode_mp3(encode_mp3(one_second, 24000), sample_rate=16000)

    assert decoded.dtype == np.float32 and decoded.ndim == 1
    assert len(decoded) / 16000 == pytest.approx(1.0, abs=0.1)
    assert 0.1 < np.abs(decoded).max() <= 1.0


def test_decode_mp3_rejects_garbage():
    with pytest.raises(TtsError):
        decode_mp3(b"not an mp3 at all")


def wait_until(condition, qapp, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


class FakeAudio:
    """합성과 재생을 흉내 내며 순서를 기록한다."""

    def __init__(self, fail_on: str | None = None):
        self.played: list[str] = []
        self.stops = 0
        self.release = threading.Event()
        self.release.set()
        self._fail_on = fail_on

    def synthesize(self, text: str, voice: Voice):
        if text == self._fail_on:
            raise TtsError("합성 실패")
        return text, 24000

    def play(self, samples, sample_rate: int) -> None:
        self.release.wait(3)
        self.played.append(samples)

    def stop(self) -> None:
        self.stops += 1
        self.release.set()


@pytest.fixture
def speaker_with(qapp):
    speakers = []

    def make(audio: FakeAudio) -> tuple[Speaker, list[str]]:
        speaker = Speaker(Voice(), synthesize=audio.synthesize, play=audio.play, stop=audio.stop)
        events: list[str] = []
        speaker.started.connect(lambda: events.append("started"))
        speaker.finished.connect(lambda: events.append("finished"))
        speaker.failed.connect(lambda message: events.append(f"failed:{message}"))
        speakers.append(speaker)
        return speaker, events

    yield make
    for speaker in speakers:
        speaker.shutdown()


def test_speaker_plays_sentences_in_order_and_reports_start_and_end(qapp, speaker_with):
    audio = FakeAudio()
    speaker, events = speaker_with(audio)

    for sentence in ("하나", "둘", "셋"):
        speaker.say(sentence)

    assert wait_until(lambda: events[-1:] == ["finished"] and len(audio.played) == 3, qapp)
    assert audio.played == ["하나", "둘", "셋"]
    assert events[0] == "started"


def test_speaker_stop_drops_what_is_waiting(qapp, speaker_with):
    audio = FakeAudio()
    audio.release.clear()  # 첫 문장을 재생 중인 상태로 붙잡아 둔다
    speaker, events = speaker_with(audio)
    for sentence in ("하나", "둘", "셋"):
        speaker.say(sentence)
    assert wait_until(lambda: events[:1] == ["started"], qapp)

    speaker.stop()

    assert wait_until(lambda: events[-1:] == ["finished"], qapp)
    assert audio.stops >= 1
    assert "둘" not in audio.played and "셋" not in audio.played


def test_speaker_reports_failure_and_keeps_going(qapp, speaker_with):
    audio = FakeAudio(fail_on="둘")
    speaker, events = speaker_with(audio)

    for sentence in ("하나", "둘", "셋"):
        speaker.say(sentence)

    assert wait_until(lambda: len(audio.played) == 2 and events[-1:] == ["finished"], qapp)
    assert audio.played == ["하나", "셋"]
    assert "failed:합성 실패" in events


def test_speaker_says_it_is_done_even_when_nothing_could_be_played(qapp, speaker_with):
    """목소리를 하나도 못 냈어도 끝났다고 알려야, 기다리던 말풍선과 마이크가 풀린다."""
    speaker, events = speaker_with(FakeAudio(fail_on="하나"))

    speaker.say("하나")

    assert wait_until(lambda: events[-1:] == ["finished"], qapp)
    assert "started" not in events


# ---------- 마이크 듣기 ----------


class FakeMicrophone:
    """정해 둔 소리를 한 번 들려주고, 그 뒤로는 계속 조용하다."""

    def __init__(self, frames: list[np.ndarray]):
        self._frames = list(frames)
        self.closed = False

    def read(self, count: int) -> np.ndarray:
        time.sleep(0.0005)
        return self._frames.pop(0) if self._frames else np.zeros(count, dtype=np.float32)

    def close(self) -> None:
        self.closed = True


class FakeTranscriber:
    def __init__(self, *texts: str):
        self._texts = list(texts)
        self.heard_seconds: list[float] = []
        self.loads = 0

    def load(self) -> None:
        self.loads += 1

    def transcribe(self, samples: np.ndarray) -> str:
        self.heard_seconds.append(len(samples) / CONFIG.sample_rate)
        return self._texts.pop(0) if self._texts else ""


@pytest.fixture
def listener_with(qapp):
    listeners = []

    def make(frames, transcriber, *, wake_enabled=True, open_microphone=None):
        microphone = FakeMicrophone(frames)
        listener = MicListener(
            transcriber,
            VoiceRouter(["모모"], wake_enabled=wake_enabled),
            open_microphone=open_microphone or (lambda config: microphone),
        )
        heard: list[tuple[str, str]] = []
        states: list[str] = []
        failures: list[str] = []
        listener.heard.connect(lambda text, via: heard.append((text, via)))
        listener.state_changed.connect(states.append)
        listener.failed.connect(failures.append)
        listeners.append(listener)
        return listener, microphone, heard, states, failures

    yield make
    for listener in listeners:
        listener.shutdown()


def test_listener_hears_a_command_addressed_by_name(qapp, listener_with):
    transcriber = FakeTranscriber("모모야 10분 뒤에 알람 맞춰 줘")
    listener, _, heard, states, _ = listener_with(silence(300) + tone(900) + silence(1200), transcriber)

    listener.start()

    assert wait_until(lambda: heard == [("10분 뒤에 알람 맞춰 줘", "wake")], qapp)
    assert "hearing" in states and "thinking" in states
    assert transcriber.heard_seconds[0] > 0.9
    assert transcriber.loads == 1  # 모델은 처음 한 번만 올린다


def test_listener_keeps_microphone_closed_until_needed(qapp, listener_with):
    opened = []

    def open_microphone(config):
        opened.append(True)
        return FakeMicrophone([])

    listener, _, heard, states, _ = listener_with([], FakeTranscriber(), wake_enabled=False, open_microphone=open_microphone)
    listener.start()
    time.sleep(0.1)
    assert opened == []

    listener.arm()

    assert wait_until(lambda: opened == [True], qapp)


def test_listener_discards_what_it_hears_while_paused(qapp, listener_with):
    transcriber = FakeTranscriber("모모야 불 켜 줘")
    listener, microphone, heard, _, _ = listener_with(silence(300) + tone(900) + silence(1200), transcriber)
    listener.pause()

    listener.start()

    assert wait_until(lambda: not microphone._frames, qapp)
    time.sleep(0.1)
    qapp.processEvents()
    assert heard == []
    assert transcriber.heard_seconds == []


def test_listener_reports_when_microphone_cannot_be_opened(qapp, listener_with):
    def broken(config):
        raise OSError("마이크 없음")

    listener, _, _, states, failures = listener_with([], FakeTranscriber(), open_microphone=broken)

    listener.start()

    assert wait_until(lambda: len(failures) == 1, qapp)
    assert "마이크" in failures[0]
    assert wait_until(lambda: states[-1:] == ["off"], qapp)
