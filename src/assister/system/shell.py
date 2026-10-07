"""PowerShell 명령을 실행한다. AI가 터미널 일을 맡을 때 쓰는 유일한 통로다.

- 지우기(Remove-Item과 rm, del 같은 별명)는 휴지통으로 보내도록 바꿔 둔다.
- 글 파일은 UTF-8로 읽고 쓴다. 그러지 않으면 방금 만든 한글 파일을 다시 읽을 때 글자가 깨진다.
- 오류가 나면 그 자리에서 멈춘다. 앞 줄이 실패했는데 뒷줄의 지우기가 엉뚱한 폴더에서 도는 일을 막는다.
- 바탕화면·문서·다운로드는 PC마다 자리가 달라서 $Desktop, $Documents, $Downloads로 미리 넣어 준다.
- 실행 전에 물어볼 만한 명령인지는 risk_reason이 낱말로 가려낸다.
"""

from __future__ import annotations

import base64
import locale
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

TIMEOUT_SEC = 30.0
KILL_WAIT_SEC = 3.0
MAX_OUTPUT_CHARS = 3000

PRELUDE = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$PSDefaultParameterValues['*:Encoding'] = 'utf8'
$Desktop = [Environment]::GetFolderPath('Desktop')
$Documents = [Environment]::GetFolderPath('MyDocuments')
$Downloads = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders' -ErrorAction SilentlyContinue).'{374DE290-123F-4565-9164-39C4925E467B}'
$Downloads = if ($Downloads) { [Environment]::ExpandEnvironmentVariables($Downloads) } else { Join-Path $HOME 'Downloads' }
function Remove-Item {
    [CmdletBinding(DefaultParameterSetName = 'Path', SupportsShouldProcess)]
    param(
        [Parameter(ParameterSetName = 'Path', Mandatory, Position = 0, ValueFromPipeline, ValueFromPipelineByPropertyName)][string[]]$Path,
        [Parameter(ParameterSetName = 'LiteralPath', Mandatory, ValueFromPipelineByPropertyName)][Alias('PSPath')][string[]]$LiteralPath,
        [switch]$Recurse,
        [switch]$Force
    )
    begin { Add-Type -AssemblyName Microsoft.VisualBasic }
    process {
        $found = if ($LiteralPath) { Resolve-Path -LiteralPath $LiteralPath } else { Resolve-Path -Path $Path }
        foreach ($item in $found) {
            if ($item.Provider.Name -ne 'FileSystem') { throw "파일과 폴더만 지울 수 있습니다: $item" }
            $full = $item.ProviderPath
            if (-not $PSCmdlet.ShouldProcess($full, '휴지통으로 보내기')) { continue }
            if (Test-Path -LiteralPath $full -PathType Container) {
                [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($full, 'OnlyErrorDialogs', 'SendToRecycleBin')
            } else {
                [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($full, 'OnlyErrorDialogs', 'SendToRecycleBin')
            }
            "휴지통으로 보냄: $full"
        }
    }
}
Set-Location $Desktop
"""

# 오류는 긴 설명 대신 한 줄로 받는다. 프로그램이 실패로 끝나면 그 종료 코드를 그대로 돌려준다.
SCRIPT = """{prelude}
try {{
{command}
}} catch {{ "오류: $($_.Exception.Message)"; exit 1 }}
if ($LASTEXITCODE) {{ exit $LASTEXITCODE }}
"""

RECYCLE = "파일이나 폴더를 휴지통으로 보냅니다"
PERMANENT = "휴지통을 거치지 않고 지우거나 비울 수 있습니다"
OVERWRITE = "있던 파일을 덮어쓰거나 옮길 수 있습니다"
INSTALL = "프로그램을 설치하거나 지웁니다"
POWER = "프로그램을 끝내거나 PC를 끕니다"
NETWORK = "인터넷에서 받아 오거나 밖으로 보냅니다"
EXEC = "다른 프로그램이나 숨은 명령을 실행합니다"
STATE = "PC의 설정이나 상태를 바꿀 수 있습니다"
SECRETS = "비밀번호나 열쇠 같은 것을 읽을 수 있습니다"


def _names(*names: str) -> str:
    """명령 이름으로만 맞춘다. 'format'이 'Format-Table'에, 'md'가 'README.md'에 걸리지 않게 한다."""
    return rf"(?<![\w.\\/-])(?:{'|'.join(names)})(?:\.exe)?(?![\w.-])"


_NEW_ITEM = _names("new-item", "ni", "mkdir", "md")
_STATE_VERBS = (
    "set|new|add|remove|clear|reset|enable|disable|unregister|restart|stop|suspend|resume|update|mount|dismount"
    "|initialize|repair|optimize|resize|grant|revoke|block|unblock|protect|unprotect|lock|unlock|publish|unpublish"
    "|connect|disconnect|send|receive|sync|checkpoint|limit|merge|edit|switch|use|debug|deny|approve|submit|request"
    "|deploy|complete|confirm|undo|redo|open|close|convert|out"
)
# ponytail: 낱말로만 가려내서 놓치는 것이 있다(처음 보는 프로그램의 지우는 옵션, 글자를 이어 붙여 만든 명령 등).
# 사용자가 '위험해 보이는 것만 확인'을 골랐다. 더 조여야 하면 조회 명령만 바로 실행하는 허용 목록으로 바꾼다.
# 위에서부터 차례로 보고, 이유를 붙인 낱말은 지워서 아래의 넓은 규칙에 다시 걸리지 않게 한다. 이유가 빈 줄은 괜찮은 명령이다.
_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), reason)
    for pattern, reason in (
        (
            _names(
                "set-location", "new-object", "new-timespan", "new-guid", "start-sleep", "out-string", "out-null",
                "out-host", "out-default", "clear-host", "add-member", "import-csv", "convert-path", "set-strictmode",
            ),
            "",
        ),
        (_names("remove-item", "rm", "del", "erase", "rd", "rmdir", "ri"), RECYCLE),
        (
            _names(
                "format", "format-volume", "clear-disk", "diskpart", "cipher", "sdelete", "clear-recyclebin",
                "clear-content", "clc", "clear-item", "cli",
            )
            + r"|(?:\.|::)delete\(",
            PERMANENT,
        ),
        (
            r">(?!>?\s*\$null)(?!&1)"
            + "|" + _NEW_ITEM + r"[^|;\n]*\s-f\w*"  # -Force를 붙이면 있던 파일을 덮어쓴다
            + "|" + _names(
                "out-file", "out-printer", "set-content", "sc", "add-content", "ac", "tee", "tee-object", "robocopy",
                "xcopy", "tar", "attrib", "icacls", "takeown", "mklink", "copy", "cp", "cpi", "move", "mv", "mi", "ren",
                "rni", r"(?:copy|move|rename|export|compress|expand|save|backup|restore)-\w+",
            )
            + r"|\[(?:system\.)?io\.",
            OVERWRITE,
        ),
        (_NEW_ITEM, ""),
        (
            _names("winget", "choco", "scoop", "msiexec", "dism", "wusa", r"(?:un)?install-\w+", r"add-appx\w*")
            + r"|(?<![\w-])(?:pip3?|npm|pnpm|yarn|uv|cargo|gem)\s+(?:install|uninstall|add|remove|i|rm)\b"
            + r"|-m\s+pip\s+(?:install|uninstall)|\.msi\b",
            INSTALL,
        ),
        (
            _names("stop-process", "spps", "kill", "taskkill", "tskill", "shutdown", "logoff", r"(?:restart|stop)-computer"),
            POWER,
        ),
        (
            _names(
                "invoke-webrequest", "iwr", "curl", "wget", "invoke-restmethod", "irm", "start-bitstransfer",
                "bitsadmin", "certutil", "send-mailmessage", "ftp", "sftp", "ssh", "scp", "ncat", "telnet",
            )
            + r"|net\.webclient|net\.http|download(?:string|file|data)|upload(?:string|file|data|values)",
            NETWORK,
        ),
        (
            _names(
                "invoke-expression", "iex", "cmd", "powershell", "pwsh", "wscript", "cscript", "mshta", "rundll32",
                "regsvr32", "start", "saps", "invoke-item", "ii", "runas", "add-type", "wmic", "schtasks",
                r"(?:invoke|start|enter|import|register)-\w+",
            )
            + r"|\s-e(?:nc\w*)?\s+[a-z0-9+/=]{12,}|frombase64string|\.(?:ps1|bat|cmd|vbs|js|hta|exe|scr|lnk)\b"
            + r"|&\s*[\"'$(\[]|\.invoke\(|\[scriptblock\]",
            EXEC,
        ),
        (
            _names("reg", "regedit", "netsh", "bcdedit", "setx", "powercfg", "net1?", "fsutil", "gpupdate", rf"(?:{_STATE_VERBS})-\w+")
            + r"|setenvironmentvariable"
            + r"|(?<![\w-])git\s+(?!(?:status|log|diff|show|blame|ls-files|rev-parse|describe|--version)\b)\w",
            STATE,
        ),
        (
            r"\.ssh|\.aws|\.gnupg|\.kube|id_rsa|id_ed25519|credential|password|passwd|secret|token|api[_-]?key"
            r"|\.env(?!\w)|login data|cookies|wallet|\.kdbx|\.pem\b|\.pfx\b|cmdkey|vaultcmd|key=clear|get-clipboard"
            r"|lsass|ntds",
            SECRETS,
        ),
    )
)  # fmt: skip


class ShellError(Exception):
    """명령을 실행조차 하지 못했을 때. 메시지는 사용자에게 그대로 보여 줄 수 있는 말이다."""


@dataclass(frozen=True)
class CommandResult:
    output: str
    exit_code: int | None  # None이면 제때 끝나지 않아 끊은 것


def risk_reason(command: str) -> str:
    """실행하기 전에 물어봐야 할 까닭. 물어볼 것이 없으면 빈 글."""
    reasons: list[str] = []
    rest = command
    for pattern, reason in _RULES:
        if pattern.search(rest):
            reasons.append(reason)
            rest = pattern.sub(" ", rest)
    if EXEC in reasons:
        # 다른 셸이나 프로그램 안에서 지우면 휴지통으로 보내도록 바꿔 둔 것이 통하지 않는다.
        reasons = [PERMANENT if reason == RECYCLE else reason for reason in reasons]
    return ", ".join(dict.fromkeys(reason for reason in reasons if reason))


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # 화면 글자표를 따르지 않는 프로그램은 Windows 기본 글자표로 내보낸다.
        return raw.decode(locale.getpreferredencoding(False), errors="replace")


def run(command: str, *, timeout: float = TIMEOUT_SEC) -> CommandResult:
    """명령을 창 없이 실행하고 화면에 나왔을 글을 돌려준다."""
    program = shutil.which("powershell")
    if program is None:
        raise ShellError("PowerShell을 찾을 수 없습니다")
    log.info("명령 실행: %s", command)
    # 따옴표가 섞인 명령도 그대로 전해지도록 통째로 부호화해서 넘긴다.
    script = SCRIPT.format(prelude=PRELUDE, command=command)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    process = subprocess.Popen(  # noqa: S603 (실행해도 되는지는 부르는 쪽이 risk_reason으로 먼저 가려낸다)
        [program, "-NoProfile", "-NonInteractive", "-OutputFormat", "Text", "-EncodedCommand", encoded],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        raw, _ = process.communicate(timeout=timeout)
        exit_code: int | None = process.returncode
    except subprocess.TimeoutExpired:
        # 딸린 프로그램까지 끊는다. 남아 있으면 출력 통로를 물고 있어 읽기가 끝나지 않는다.
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False)  # noqa: S603, S607
        try:
            raw, _ = process.communicate(timeout=KILL_WAIT_SEC)
        except subprocess.TimeoutExpired:
            raw = b""
        exit_code = None
    text = _decode(raw).strip()
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "\n…(뒤는 잘림)"
    return CommandResult(text, exit_code)
