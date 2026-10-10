"""부품을 조립해 앱을 띄운다."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import fields, replace
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QProcess, Qt, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from assister import paths
from assister.ai.agy import AgyEngine
from assister.ai.chat import ChatSession
from assister.ai.engine import ChatEngine, EngineError, OllamaEngine, start_ollama
from assister.ai.gemini import GeminiEngine
from assister.ai.prompt import WRITER
from assister.ai.search import search
from assister.ai.tools import ToolBox
from assister.ai.worker import ChatWorker
from assister.character.art import generate_placeholder_pack
from assister.character.pack import CharacterPack, PackError, Voice, list_packs, load_pack, save_profile
from assister.character.stage import CharacterStage
from assister.config import Settings, SettingsError, load_settings, save_settings
from assister.control import elements
from assister.control.agent import ControlAgent
from assister.control.executor import Win32Executor
from assister.growth.bond import (
    Mood,
    idle_expression,
    level_for,
    mood_at,
    newly_unlocked,
    points_to_next,
    reaction,
    tone_note,
)
from assister.memory.backup import BackupError, contents, export_data, import_data
from assister.memory.store import MemoryStore
from assister.screen.capture import take_snapshot
from assister.screen.privacy import DEFAULT_SENSITIVE
from assister.screen.watcher import ScreenWatcher
from assister.system import autostart, documents, shell
from assister.system.hotkey import GlobalHotkey, HotkeyError
from assister.system.settings_dialog import SettingsDialog
from assister.system.tray import AppMenu
from assister.tasks.repository import TaskRepository
from assister.tasks.service import Reply, TaskService, format_time
from assister.voice.listener import MicListener
from assister.voice.sentences import SentenceSplitter, speakable
from assister.voice.stt import Transcriber
from assister.voice.tts import Speaker
from assister.voice.wake import VoiceRouter, spoken_answer

log = logging.getLogger(__name__)

ALARM_CHECK_MS = 1000
MOOD_CHECK_MS = 60_000
RESTART_WAIT_MS = 5000
WAKING_UP = "잠깐만요, 아직 머리를 깨우는 중이에요. 처음에는 1분쯤 걸릴 수 있어요."
BOND_KEY = "bond_points"
WAKE_ACK = "네?"
STOP_KEY = "Esc"
HISTORY_SHOWN = 60  # 켤 때 패널의 대화 기록에 채워 둘 예전 말의 수
SCREEN_NOTES_SHOWN = 30
SIMPLE_COMMAND_HINT = "그동안 알람과 할 일은 '10분 뒤 빨래', '할 일 추가 우유 사기'처럼 말하면 돼요."


def single_instance_lock(data_dir: Path, wait_ms: int = RESTART_WAIT_MS) -> QLockFile | None:
    """앱이 하나만 떠 있게 하는 잠금. 이미 떠 있으면 None.

    둘이 뜨면 단축키, 마이크, 기억 파일을 서로 뺏는다. 다시 시작할 때는 앞의 앱이
    꺼지는 동안 잠깐 기다려 준다.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_dir / "running.lock"))
    lock.setStaleLockTime(0)  # 시간이 지났다고 남의 잠금을 뺏지 않는다. 죽은 프로세스의 잠금은 Qt가 알아서 치운다.
    return lock if lock.tryLock(wait_ms) else None


def pick_save_path() -> str:
    """기억을 내보낼 파일 자리를 묻는다. 취소하면 빈 글."""
    return QFileDialog.getSaveFileName(None, "기억 내보내기", "바탕화면친구-기억.zip", "기억 파일 (*.zip)")[0]


def pick_open_path() -> str:
    return QFileDialog.getOpenFileName(None, "기억 가져오기", "", "기억 파일 (*.zip)")[0]


def confirm(text: str) -> bool:
    """되돌릴 수 없는 일을 하기 전에 묻는다. 기본 선택은 '아니요'다."""
    yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
    return QMessageBox.question(None, "확인", text, yes | no, no) == yes


def restart() -> None:
    """같은 앱을 새로 띄우고 지금 것은 끈다. 새 앱은 검은 명령 창 없이 뜬다."""
    QProcess.startDetached(autostart.windowless_python(), ["-m", "assister"], str(paths.project_root()))
    QApplication.quit()


def make_engine(settings: Settings) -> ChatEngine:
    """설정에서 고른 AI 엔진. Gemini는 대화와 화면 그림을 Google 서버로 보낸다."""
    if settings.engine == "gemini":
        return GeminiEngine(settings.gemini_model)
    if settings.engine == "agy":
        return AgyEngine(settings.agy_model)
    return OllamaEngine(
        settings.model, base_url=settings.ollama_url, num_ctx=settings.num_ctx, start_server=start_ollama
    )


def load_character(settings: Settings) -> CharacterPack:
    """캐릭터 폴더를 불러온다. 기본 캐릭터가 없으면 임시 그림으로 만든다."""
    root = paths.characters_dir() / settings.character
    default_root = paths.characters_dir() / Settings.character
    if not (default_root / "outfits").is_dir():
        generate_placeholder_pack(default_root)
    try:
        return load_pack(root)
    except PackError as error:
        if root == default_root:
            raise
        log.warning("캐릭터 '%s'을(를) 불러오지 못해 기본 캐릭터를 씁니다: %s", settings.character, error)
        return load_pack(default_root)


class DesktopFriend(QObject):
    """engine, speaker, listener를 넘기지 않으면 진짜(Ollama, Edge TTS, 마이크)를 만든다."""

    def __init__(
        self,
        app: QApplication,
        *,
        engine: ChatEngine | None = None,
        speaker: Speaker | None = None,
        listener: MicListener | None = None,
        watcher: ScreenWatcher | None = None,
        agent: ControlAgent | None = None,
    ) -> None:
        super().__init__()
        self._app = app
        self._notices: list[str] = []
        self._closed = False
        self._settings = self._load_settings()

        self.pack = load_character(self._settings)
        self.repository = TaskRepository(paths.tasks_db_path())
        self.memory = MemoryStore(paths.memory_db_path())
        self.tasks = TaskService(self.repository)

        settings = self._settings
        self._engine = engine or make_engine(settings)
        # 그림을 읽지 못하는 엔진(agy)을 쓸 때는 화면 보기와 PC 조작을 내 PC의 Ollama가 맡는다.
        sees = getattr(self._engine, "sees_images", True)
        self._eyes = self._engine if sees else make_engine(replace(settings, engine="ollama"))
        session = ChatSession(
            self._engine,
            self.memory,
            # 대화 작업 스레드에서 불린다. agent는 아래에서 만들지만 불릴 때는 이미 있다.
            ToolBox(
                self.repository,
                self.memory,
                start_control=lambda goal: self.agent.run(goal),
                search=search,
                shell=shell.run,
                confirm=lambda text: self.chat.confirm(text),
                write=lambda topic: self._engine.chat([{"role": "user", "content": WRITER.format(topic=topic)}]),
                save_document=lambda kind, name, text: documents.save_docx(documents.known_folder(kind), name, text),
            ),
            character_name=self.pack.name,
            persona=self.pack.persona,
            extra_context=lambda: tone_note(level=level_for(self._points), mood=self._mood()),
        )
        self._points = self.memory.get_state(BOND_KEY, 0)
        self._news = ""  # 레벨이 올랐을 때 다음 답 끝에 덧붙일 소식
        self.chat = ChatWorker(session)
        self.speaker = speaker or Speaker(self.pack.voice)
        self.listener = listener or MicListener(
            Transcriber(settings.stt_model, device=settings.stt_device, hotwords=" ".join(self.pack.wake_names)),
            VoiceRouter(self.pack.wake_names, wake_enabled=settings.wake_word_enabled),
        )
        self.watcher = watcher or ScreenWatcher(
            self._eyes,
            self.memory,
            interval_sec=settings.screen_interval_sec,
            retention_days=settings.screen_retention_days,
            keywords=self._hidden_keywords,
            width=settings.screen_width,
            rest_when_fullscreen=settings.rest_when_fullscreen,
            # 캐릭터가 답하거나 말하거나 PC를 조작하는 동안에는 화면 분석으로 AI를 붙잡지 않는다.
            is_busy=lambda: self._replying or self._voice_busy or self.agent.running,
        )
        self.agent = agent or ControlAgent(
            self._eyes,
            Win32Executor(),
            take_snapshot=take_snapshot,
            keywords=self._hidden_keywords,
            width=settings.control_width,
            max_steps=settings.control_max_steps,
            # 누를 수 있는 것의 목록에서 고르는 일은 글만 읽으면 되니 대화 엔진이 맡는다.
            chooser=self._engine,
            list_elements=elements.look,
        )
        self._stop_hotkey: GlobalHotkey | None = None
        self._permission: QMessageBox | None = None
        self._permission_answer: Callable[[bool], None] | None = None
        if not settings.screen_watch_enabled:
            self.watcher.set_enabled(False)
        self._last_offer = datetime.now()

        self.stage = CharacterStage(self.pack, settings.outfit)
        self.menu = AppMenu(
            self.pack,
            outfit=settings.outfit,
            do_not_disturb=settings.do_not_disturb,
            tts_enabled=settings.tts_enabled,
            wake_word_enabled=settings.wake_word_enabled,
            screen_watch_enabled=settings.screen_watch_enabled,
            props=settings.props,
            level=level_for(self._points),
            points_left=points_to_next(self._points),
        )
        self.stage.character.set_props(settings.props)
        self._hotkeys = [
            hotkey
            for hotkey in (
                self._register_hotkey(settings.summon_hotkey, self.stage.toggle),
                self._register_hotkey(settings.voice_hotkey, self.listener.arm),
            )
            if hotkey is not None
        ]

        # 지금 만들고 있는 답의 상태
        self._asked = ""
        self._answer = ""
        self._splitter = SentenceSplitter()
        self._replying = False
        self._voice_busy = False
        self._model_ready = False

        self._connect()
        self._alarm_timer = QTimer(self)
        self._alarm_timer.timeout.connect(self._check_alarms)
        self._mood_timer = QTimer(self)
        self._mood_timer.timeout.connect(self._show_mood)

    def _connect(self) -> None:
        self.stage.submitted.connect(self._on_submitted)
        self.stage.clicked.connect(self.stage.summon)
        self.stage.mic_clicked.connect(self.listener.arm)
        self.stage.menu_requested.connect(self.menu.popup)
        self.stage.moved.connect(lambda x, y: self._update_settings(panel_position=(x, y)))
        self.stage.resized.connect(lambda width, height: self._update_settings(panel_size=(width, height)))
        # 왼쪽 자리로 되돌리면 자리와 크기를 잊는다. 그래야 화면 해상도가 바뀌어도 1/4에 맞춰진다.
        self.stage.docked.connect(lambda: self._update_settings(panel_position=None, panel_size=None))

        self.menu.do_not_disturb_toggled.connect(self._on_do_not_disturb)
        self.menu.tts_toggled.connect(self._on_tts_toggled)
        self.menu.wake_toggled.connect(self._on_wake_toggled)
        self.menu.outfit_chosen.connect(self._on_outfit)
        self.menu.prop_toggled.connect(self._on_prop)
        self.menu.todos_requested.connect(lambda: self._on_submitted("할 일 뭐 남았어?"))
        self.menu.tray_clicked.connect(self.stage.summon)
        self.menu.settings_requested.connect(self._open_settings)
        self.menu.export_requested.connect(self._export_memory)
        self.menu.import_requested.connect(self._import_memory)
        self.menu.quit_requested.connect(self._app.quit)
        self._app.aboutToQuit.connect(self.shutdown)

        self.chat.ready.connect(self._on_model_ready)
        self.chat.warm_up_failed.connect(self._on_warm_up_failed)
        self.chat.started.connect(self._on_answer_started)
        self.chat.delta.connect(self._on_answer_delta)
        self.chat.finished.connect(self._on_answer_finished)
        self.chat.failed.connect(self._on_answer_failed)
        self.chat.confirm_needed.connect(lambda text: self._ask_permission(text, self.chat.answer))
        self.chat.action_taken.connect(self.stage.note)

        self.speaker.started.connect(self._on_voice_started)
        self.speaker.finished.connect(self._on_voice_finished)

        self.listener.heard.connect(self._on_heard)
        self.listener.state_changed.connect(self.stage.input_bar.set_mic_state)
        self.listener.failed.connect(self._on_microphone_failed)

        self.agent.started.connect(self._on_control_started)
        self.agent.step.connect(lambda text: self.stage.say(text, "thinking"))
        self.agent.confirm_needed.connect(lambda text: self._ask_permission(text, self.agent.answer))
        self.agent.finished.connect(self._on_control_finished)

        self.watcher.noted.connect(self._on_screen_noted)
        self.watcher.state_changed.connect(self.stage.input_bar.set_eye_state)
        self.menu.screen_toggled.connect(self._on_screen_toggled)
        self.stage.input_bar.eye_clicked.connect(
            lambda: self._on_screen_toggled(not self._settings.screen_watch_enabled)
        )

    def start(self) -> None:
        self.stage.load_history((message.role, message.content) for message in self.memory.recent_messages(HISTORY_SHOWN))
        self.stage.show(self._settings.panel_position, self._settings.panel_size)
        self._show_lists()
        self.menu.show_tray()
        self._alarm_timer.start(ALARM_CHECK_MS)
        self._mood_timer.start(MOOD_CHECK_MS)
        self._show_mood()
        self.chat.warm_up()
        if self._eyes is not self._engine:
            # 대화 엔진과 따로 도는 Ollama도 미리 켜고 모델을 올려 둔다. 오래 걸려서 뒤에서 한다.
            threading.Thread(target=self._warm_eyes, name="warm-eyes", daemon=True).start()
        self.listener.start()
        self.stage.input_bar.set_eye_state("watching" if self._settings.screen_watch_enabled else "off")
        self.watcher.start()
        self._say(Reply("\n".join([self._greeting(), *self._notices]), "smile"))

    def _warm_eyes(self) -> None:
        try:
            self._eyes.warm_up()
        except EngineError as error:
            log.warning("화면을 읽을 AI를 올리지 못했습니다: %s", error)

    def _greeting(self) -> str:
        """켤 때 하는 인사. 남은 할 일과 알람이 있으면 몇 개인지 알려 준다."""
        hello = f"안녕하세요, {self.pack.name}예요!"
        todos, alarms = len(self.repository.list_todos()), len(self.repository.list_pending_alarms())
        pending = [text for count, text in ((todos, f"할 일 {todos}개"), (alarms, f"알람 {alarms}개")) if count]
        return f"{hello} 남은 {', '.join(pending)} 있어요." if pending else hello

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._alarm_timer.stop()
        self._mood_timer.stop()
        for hotkey in self._hotkeys:
            hotkey.release()
        self.agent.stop()
        self.agent.join()
        self._release_stop_key()
        self.watcher.shutdown()
        self.listener.shutdown()
        self.speaker.shutdown()
        self.chat.shutdown()
        self._engine.close()
        if self._eyes is not self._engine:
            self._eyes.close()
        self.menu.close()
        self.stage.close()
        self.repository.close()
        self.memory.close()

    # ---------- 설정 ----------

    def _load_settings(self) -> Settings:
        try:
            return load_settings(paths.settings_path())
        except SettingsError as error:
            log.warning("%s", error)
            self._notices.append("설정 파일을 읽지 못해 기본 설정으로 시작했어요.")
            return Settings()

    def _update_settings(self, **changes) -> None:
        self._settings = replace(self._settings, **changes)
        try:
            save_settings(paths.settings_path(), self._settings)
        except SettingsError as error:
            log.warning("%s", error)
            self._say(Reply("설정을 저장하지 못했어요.", "sad"))

    def _open_settings(self) -> None:
        self._settings_dialog = SettingsDialog(
            self._settings,
            self.pack,
            autostart=autostart.is_enabled(),
            characters=list_packs(paths.characters_dir()),
        )
        self._settings_dialog.accepted.connect(self._apply_settings)
        self._settings_dialog.preview_requested.connect(self._preview_voice)
        # 들어 보기만 하고 취소했으면 원래 목소리로 돌려놓는다.
        self._settings_dialog.rejected.connect(lambda: setattr(self.speaker, "voice", self.pack.voice))
        self._settings_dialog.open()

    def _preview_voice(self, voice: Voice) -> None:
        self.speaker.stop()
        self.speaker.voice = voice
        self.speaker.say(f"안녕하세요, {self._settings_dialog.name.text().strip() or self.pack.name}예요.")

    def _apply_settings(self) -> None:
        """설정 창에서 고친 것을 파일에 쓰고 앱을 다시 시작한다. 단축키·목소리·주기는 켤 때 읽기 때문이다."""
        settings, profile = self._settings_dialog.result_values()
        if self._settings_dialog.character_changed():
            # 다른 캐릭터로 바꿨다. 창에 적힌 이름·말투는 지금 캐릭터의 것이니 어디에도 쓰지 않는다.
            # 옷과 소품도 캐릭터마다 다르므로 새 캐릭터의 기본으로 돌린다.
            settings = replace(settings, outfit=Settings.outfit, props=())
        else:
            try:
                save_profile(self.pack.root, profile)
            except PackError as error:
                log.warning("%s", error)
                self._say(Reply("캐릭터 설정을 저장하지 못했어요.", "sad"))
                return
        self._update_settings(**{field.name: getattr(settings, field.name) for field in fields(settings)})
        try:
            autostart.set_enabled(self._settings_dialog.autostart.isChecked())
        except OSError as error:
            log.warning("시작프로그램에 등록하지 못했습니다: %s", error)
        self.shutdown()  # 단축키를 먼저 놓아야 새로 뜨는 앱이 잡을 수 있다
        restart()

    def _export_memory(self) -> None:
        path = pick_save_path()
        if not path:
            return
        try:
            export_data(paths.data_dir(), Path(path))
        except BackupError as error:
            log.warning("%s", error)
            self._say(Reply(str(error), "sad"))
        else:
            self._say(Reply("기억을 파일에 담았어요. 새 PC에서 '기억 가져오기'로 불러오면 돼요.", "smile"))

    def _import_memory(self) -> None:
        path = pick_open_path()
        if not path:
            return
        try:
            contents(Path(path))  # 앱을 끄기 전에, 가져올 수 있는 파일인지 먼저 본다
        except BackupError as error:
            self._say(Reply(str(error), "sad"))
            return
        if not confirm("지금의 기억·할 일·설정을 이 파일의 것으로 바꿉니다.\n지금 것은 사라집니다. 계속할까요?"):
            return
        self.shutdown()  # DB 파일을 닫아야 덮어쓸 수 있다
        try:
            import_data(Path(path), paths.data_dir())
        except BackupError as error:
            log.error("%s", error)
        restart()

    def _register_hotkey(self, text: str, action) -> GlobalHotkey | None:
        try:
            hotkey = GlobalHotkey(text, self)
        except HotkeyError as error:
            log.warning("%s", error)
            self._notices.append(f"단축키 {text}은(는) 쓸 수 없어요.")
            return None
        hotkey.pressed.connect(action)
        return hotkey

    # ---------- 말하기 ----------

    def _voice_on(self) -> bool:
        # 방해금지 중에는 소리를 내지 않는다.
        return self._settings.tts_enabled and not self._settings.do_not_disturb

    def _speak(self, sentences: list[str]) -> None:
        if not self._voice_on():
            return
        for sentence in sentences:
            if text := speakable(sentence):
                self._voice_busy = True
                self.speaker.say(text)

    def _say(self, reply: Reply, *, aloud: bool = False) -> None:
        """AI를 거치지 않는 말(인사, 알람, 안내)을 보여 준다. aloud면 목소리로도 읽는다."""
        if aloud and self._voice_on():
            self.stage.hold(reply.text, reply.expression)
            self._speak([reply.text])
        else:
            self.stage.say(reply.text, reply.expression)

    def _on_voice_started(self) -> None:
        # 자기 목소리를 마이크로 다시 듣지 않게 한다.
        self.listener.pause()
        self.stage.set_expression("talking")

    def _on_voice_finished(self) -> None:
        self._voice_busy = False
        self.listener.resume()
        if self._permission is not None:
            self.listener.arm()  # 물음을 다 읽었으니 이제 대답을 듣는다
        if not self._replying:
            self._settle_face()
            self.stage.release()

    def _settle_face(self) -> None:
        """말을 마친 뒤, 방금 한 말에 어울리는 표정을 한동안 짓는다."""
        if face := reaction(self._answer):
            self.stage.set_expression(face)

    # ---------- 듣기와 답하기 ----------

    def _on_submitted(self, text: str) -> None:
        self.stage.show_user(text)
        self._grow()
        # 새로 말을 걸면 하던 말을 끊는다.
        self.speaker.stop()
        if self._model_ready:
            self.stage.think()
        else:
            self.stage.hold(WAKING_UP, "sleepy")
        self.chat.ask(text)

    def _on_heard(self, text: str, via: str) -> None:
        if via == "wake_only":
            self.stage.say(WAKE_ACK, "smile")
            return
        if self._permission is not None:
            self._hear_permission(text)
            return
        self.stage.input_bar.show_heard(text)
        self._on_submitted(text)

    def _on_answer_started(self, text: str) -> None:
        self._asked = text
        self._answer = ""
        self._splitter = SentenceSplitter()
        self._replying = True
        self.stage.think()

    def _on_answer_delta(self, piece: str) -> None:
        self._answer += piece
        self.stage.stream(self._answer)
        self._speak(self._splitter.feed(piece))

    def _on_answer_finished(self, reply: str) -> None:
        self._replying = False
        news, self._news = self._news, ""
        self.stage.stream(f"{reply}\n{news}" if news else reply)
        self._speak([*self._splitter.flush(), news])
        self._answer = reply
        # 목소리로 읽는 중이면 다 읽을 때까지 말하는 중으로 둔다.
        if not self._voice_busy:
            self._settle_face()
            self.stage.release()
        self._show_lists()

    def _on_answer_failed(self, message: str) -> None:
        self._replying = False
        # AI가 꺼져 있어도 간단한 알람·할 일 명령은 알아듣는다.
        fallback = self.tasks.handle(self._asked, datetime.now()) if self._asked else None
        self._say(fallback or Reply(message, "sad"), aloud=fallback is not None)
        self._show_lists()

    def _on_model_ready(self) -> None:
        self._model_ready = True

    def _on_warm_up_failed(self, message: str) -> None:
        # 모델을 못 올렸어도 '깨우는 중'이라고 계속 말하지는 않는다. 다음 답에서 다시 시도하고, 안 되면 그때 이유를 알린다.
        self._model_ready = True
        self._say(Reply(f"{message}\n{SIMPLE_COMMAND_HINT}", "sad"))

    def _on_microphone_failed(self, message: str) -> None:
        self.menu.set_wake_checked(False)
        self._update_settings(wake_word_enabled=False)
        self._say(Reply(message, "sad"))

    # ---------- 친밀도와 기분 ----------

    def _mood(self) -> Mood:
        now = datetime.now()
        return mood_at(now, self.memory.count_user_messages_on(now))

    def _show_mood(self) -> None:
        mood = self._mood()
        self.stage.set_idle_expression(idle_expression(mood))
        left = points_to_next(self._points)
        progress = f"다음 레벨까지 {left}" if left is not None else "가장 높은 레벨"
        self.stage.panel.show_status(f"친밀도 레벨 {level_for(self._points)} · {progress} · {mood.value}")

    def _show_lists(self) -> None:
        """패널의 할 일·알람과 오늘 화면 기록을 지금 것으로 바꾼다."""
        now = datetime.now()
        todos = [todo.text for todo in self.repository.list_todos()]
        alarms = [
            f"{'매일 ' if alarm.repeat == 'daily' else ''}{format_time(alarm.due_at, alarm.due_at if alarm.repeat == 'daily' else now)} {alarm.message}"
            for alarm in self.repository.list_pending_alarms()
        ]
        notes = [f"{format_time(note.created_at, now)} {note.summary}" for note in self.memory.screen_notes_on(now)]
        self.stage.panel.show_tasks(todos, alarms)
        self.stage.panel.show_screen_notes(notes[-SCREEN_NOTES_SHOWN:][::-1])

    def _grow(self) -> None:
        """사용자가 한 마디 할 때마다 친밀도가 1 오른다. 레벨이 오르면 옷과 소품이 열린다."""
        old = level_for(self._points)
        self._points += 1
        self.memory.set_state(BOND_KEY, self._points)
        new = level_for(self._points)
        self.menu.set_level(new, points_to_next(self._points))
        self._show_mood()
        if new > old:
            opened = newly_unlocked(self.pack, old_level=old, new_level=new)
            self._news = f"친밀도 레벨 {new}이 됐어요!" + (f" 새로 열린 것: {', '.join(opened)}" if opened else "")

    def _on_prop(self, prop: str, worn: bool) -> None:
        props = tuple(item for item in self._settings.props if item != prop) + ((prop,) if worn else ())
        self.stage.character.set_props(props)
        self._update_settings(props=props)

    # ---------- PC 제어 ----------

    def _on_control_started(self, goal: str) -> None:
        """조작하는 동안에만 Esc를 멈춤 키로 잡는다. 멈춤 키를 못 잡으면 조작도 하지 않는다."""
        try:
            self._stop_hotkey = GlobalHotkey(STOP_KEY, self, allow_bare=True)
        except HotkeyError as error:
            log.warning("%s", error)
            self.agent.stop()
            self._say(Reply("멈춤 키(Esc)를 쓸 수 없어서 대신 조작하지 않을게요.", "sad"))
            return
        self._stop_hotkey.pressed.connect(self.agent.stop)
        # 패널이 화면의 1/4을 덮고 있으면 그 밑을 누를 수 없으니, 조작하는 동안에는 비켜 준다.
        self.stage.hide()

    def _release_stop_key(self) -> None:
        if self._stop_hotkey is not None:
            self._stop_hotkey.release()
            self._stop_hotkey = None

    def _ask_permission(self, text: str, answer: Callable[[bool], None]) -> None:
        """위험한 일은 무엇을 할지 보여 주고 허락을 받는다. 버튼을 누르거나 '허락', '안 돼'라고 말해서 답한다."""
        yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
        box = QMessageBox(QMessageBox.Icon.Question, self.pack.name, f"이 작업을 할까요?\n\n{text}", yes | no)
        box.button(yes).setText("허락")
        box.button(no).setText("안 함")
        box.setDefaultButton(no)
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        box.finished.connect(lambda result: self._on_permission_answered(box, result == yes, answer))
        # ponytail: 한 번에 하나만 묻는다. PC 조작과 명령 실행이 동시에 물으면 앞의 창은 버튼으로만 답할 수 있다.
        self._permission, self._permission_answer = box, answer
        # 명령은 소리로 읽어 봐야 알아듣기 어려우니 첫 줄(까닭)만 말한다.
        ask = f"{text.splitlines()[0]} 해도 될까요? '허락'이나 '안 돼'라고 말해 주세요."
        self._say(Reply(ask, "thinking"), aloud=True)
        self.listener.arm()
        box.open()

    def _on_permission_answered(self, box: QMessageBox, allowed: bool, answer: Callable[[bool], None]) -> None:
        if self._permission is box:
            self._permission = None
        answer(allowed)

    def _hear_permission(self, text: str) -> None:
        """허락을 기다리는 동안 들은 말은 대답으로만 듣는다. AI에게 넘기지 않는다."""
        said = spoken_answer(text)
        if said is None:
            self.listener.arm()  # 못 알아들었으면 한 번 더 듣는다
            return
        self._permission.button(QMessageBox.StandardButton.Yes if said else QMessageBox.StandardButton.No).click()

    def _on_control_finished(self, message: str, ok: bool) -> None:
        self._release_stop_key()
        if self._permission is not None and self._permission_answer == self.agent.answer:
            # Esc로 멈췄을 때 떠 있던 확인 창을 닫는다. 닫히면서 거절로 답한다.
            self._permission.close()
        self.memory.add_message("assistant", message, datetime.now())
        self.stage.reveal()
        self._say(Reply(message, "excited" if ok else "sad"), aloud=True)

    # ---------- 화면 보기 ----------

    def _hidden_keywords(self) -> tuple[str, ...]:
        """가릴 창을 고르는 낱말. 기본으로 가리는 것에 사용자가 설정에 적은 차단 목록을 더한다."""
        return (*DEFAULT_SENSITIVE, *self._settings.blocked_apps)

    def _on_screen_noted(self, summary: str, offer: str) -> None:
        """화면을 보고 도울 일이 보이면 가끔 먼저 말을 건다."""
        self._show_lists()
        per_hour = self._settings.proactive_per_hour
        if not offer or per_hour <= 0 or self._settings.do_not_disturb or self._replying or self._voice_busy:
            return
        now = datetime.now()
        if now - self._last_offer < timedelta(hours=1) / per_hour:
            return
        self._last_offer = now
        # 사용자가 이어서 "응"이라고만 답해도 무슨 얘기인지 알 수 있게 대화에 남긴다.
        self.memory.add_message("assistant", offer, now)
        self._say(Reply(offer, "smile"), aloud=True)

    def _on_screen_toggled(self, enabled: bool) -> None:
        self._update_settings(screen_watch_enabled=enabled)
        self.menu.set_screen_checked(enabled)
        self.watcher.set_enabled(enabled)
        self.stage.input_bar.set_eye_state("watching" if enabled else "off")
        self._say(Reply("화면을 보고 있을게요." if enabled else "이제 화면을 보지 않을게요.", "smile"))

    # ---------- 알람, 메뉴 ----------

    def _check_alarms(self) -> None:
        for reply in self.tasks.ring_due_alarms(datetime.now()):
            self.stage.reveal()  # 패널을 숨겨 뒀어도 알람은 보여 준다
            self._show_lists()
            self._say(reply, aloud=True)
            # 목소리를 꺼 뒀으면 소리로만 알린다. 방해금지 중에는 글로만 알린다.
            if not self._voice_on() and not self._settings.do_not_disturb:
                self._app.beep()

    def _on_do_not_disturb(self, enabled: bool) -> None:
        self._update_settings(do_not_disturb=enabled)
        if enabled:
            self.speaker.stop()
        self._say(Reply("조용히 있을게요." if enabled else "다시 말 걸어도 되죠?", "sleepy" if enabled else "smile"))

    def _on_tts_toggled(self, enabled: bool) -> None:
        self._update_settings(tts_enabled=enabled)
        if not enabled:
            self.speaker.stop()
        self._say(Reply("이제 목소리로도 답할게요." if enabled else "이제 글로만 답할게요.", "smile"))

    def _on_wake_toggled(self, enabled: bool) -> None:
        self._update_settings(wake_word_enabled=enabled)
        self.listener.set_wake_enabled(enabled)
        name = self.pack.name
        self._say(Reply(f"'{name}'라고 부르면 들을게요." if enabled else "이름을 불러도 듣지 않을게요. 마이크를 닫았어요.", "smile"))

    def _on_outfit(self, outfit: str) -> None:
        self.stage.character.set_outfit(outfit)
        self._update_settings(outfit=outfit)
        self._say(Reply("어때요, 잘 어울려요?", "excited"))
