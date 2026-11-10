import os
import subprocess
import json
import logging
import time
from dotenv import load_dotenv
from telegram import Update
from telegram.request import HTTPXRequest
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

import audit

# Carrega as variáveis do arquivo /opt/homelab-bot/.env
load_dotenv("/opt/homelab-bot/.env")

# ================= CONFIGURAÇÕES =================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ALLOWED_USER_ID = 6929102708  # Seu ID numérico do Telegram
PROXMOX_HOST = "192.168.30.250"
MODEL = "deepseek-chat"

# Mapeamento de containers/VMs permitidos
ALLOWED_TARGETS = {
    "100": "Web Server / Nginx",
    "101": "Database / Postgres",
    "102": "Serviços Docker"
}
# =================================================

logging.basicConfig(level=logging.INFO)
client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

# Definição estrita das ferramentas para a API
tools = [
    {
        "type": "function",
        "function": {
            "name": "run_system_update",
            "description": "Executa atualização de pacotes (apt update e dist-upgrade) em containers LXC específicos ou em todos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vmid": {
                        "type": "string",
                        "description": "O ID numérico do container LXC (ex: '100', '101'). Use 'all' para todos os hosts permitidos.",
                    }
                },
                "required": ["vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_disk_usage",
            "description": "Verifica o espaço livre em disco (df -h) no container Alpine local do bot ou em um container LXC específico.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "O alvo da checagem: 'local' para o container do bot, ou o vmid ('100', '101', etc). Padrão é 'local'.",
                    }
                },
            },
        },
    }
]

TOOL_NAMES = [tool["function"]["name"] for tool in tools]

SYSTEM_PROMPT = f"Você é o orquestrador do homelab Proxmox. Hosts disponíveis: {json.dumps(ALLOWED_TARGETS)}. Se o usuário solicitar atualização de sistema ou checagem de pacotes/disco, invoque as ferramentas apropriadas."

def apt_update_command(vmid: str) -> str:
    return f"pct exec {vmid} -- sh -c 'DEBIAN_FRONTEND=noninteractive apt-get update && DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y'"

def tool_audit_fields(fn_name: str, args: dict) -> dict:
    if fn_name == "run_system_update":
        vmid = args.get("vmid")
        return {
            "target": vmid,
            "resolved_target": PROXMOX_HOST,
            "command": apt_update_command(vmid) if vmid and vmid != "all" else None,
            "transport": "ssh",
            "sensitive_path": False,
        }
    if fn_name == "check_disk_usage":
        target = args.get("target", "local")
        if target == "local":
            return {
                "target": "local",
                "resolved_target": "local",
                "command": "df -h",
                "transport": "local",
                "sensitive_path": False,
            }
        return {
            "target": target,
            "resolved_target": PROXMOX_HOST,
            "command": f"pct exec {target} -- df -h",
            "transport": "ssh",
            "sensitive_path": False,
        }
    return {}

def execute_apt_update(vmid: str) -> str:
    """Dispara via SSH no nó do Proxmox a atualização do container via pct exec."""
    if vmid == "all":
        targets = list(ALLOWED_TARGETS.keys())
    elif vmid in ALLOWED_TARGETS:
        targets = [vmid]
    else:
        return f"Erro: ID {vmid} não está na lista de hosts permitidos."

    audit_context = audit.get_tool_context()
    tool_name = audit_context.get("tool") or "run_system_update"
    tool_call_id = audit_context.get("tool_call_id")
    step = audit_context.get("step")

    logs = []
    for target in targets:
        name = ALLOWED_TARGETS[target]
        remote_cmd = apt_update_command(target)
        cmd = ["ssh", "-o", "StrictHostKeyChecking=accept-new", f"root@{PROXMOX_HOST}", remote_cmd]
        started = time.monotonic()

        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300)
            audit.log_command_result(
                tool=tool_name,
                tool_call_id=tool_call_id,
                step=step,
                target=target,
                resolved_target=PROXMOX_HOST,
                transport="ssh",
                command=remote_cmd,
                executed_argv=cmd,
                exit_code=res.returncode,
                timed_out=False,
                duration_ms=int((time.monotonic() - started) * 1000),
                stdout=res.stdout.strip(),
                stderr=res.stderr.strip(),
            )
            if res.returncode == 0:
                logs.append(f"Host {target} ({name}): Sucesso.")
            else:
                logs.append(f"Host {target} ({name}): Falha (exit code {res.returncode})\nStderr: {res.stderr.strip()}")
        except subprocess.TimeoutExpired:
            audit.log_command_result(
                tool=tool_name,
                tool_call_id=tool_call_id,
                step=step,
                target=target,
                resolved_target=PROXMOX_HOST,
                transport="ssh",
                command=remote_cmd,
                executed_argv=cmd,
                exit_code=None,
                timed_out=True,
                duration_ms=int((time.monotonic() - started) * 1000),
                stdout="",
                stderr="",
                error="Tempo limite esgotado (timeout 300s).",
            )
            logs.append(f"Host {target} ({name}): Tempo limite esgotado (timeout 300s).")
        except Exception as e:
            audit.log_command_result(
                tool=tool_name,
                tool_call_id=tool_call_id,
                step=step,
                target=target,
                resolved_target=PROXMOX_HOST,
                transport="ssh",
                command=remote_cmd,
                executed_argv=cmd,
                exit_code=None,
                timed_out=False,
                duration_ms=int((time.monotonic() - started) * 1000),
                stdout="",
                stderr="",
                error=str(e),
            )
            logs.append(f"Host {target} ({name}): Exceção: {str(e)}")

    return "\n\n".join(logs)

def execute_disk_usage(target: str = "local") -> str:
    """Verifica espaço em disco local ou em containers remotos via SSH/pct."""
    audit_context = audit.get_tool_context()
    tool_name = audit_context.get("tool") or "check_disk_usage"
    tool_call_id = audit_context.get("tool_call_id")
    step = audit_context.get("step")
    started = time.monotonic()
    try:
        if target == "local":
            cmd = ["df", "-h"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
            audit.log_command_result(
                tool=tool_name,
                tool_call_id=tool_call_id,
                step=step,
                target="local",
                resolved_target="local",
                transport="local",
                command="df -h",
                executed_argv=cmd,
                exit_code=res.returncode,
                timed_out=False,
                duration_ms=int((time.monotonic() - started) * 1000),
                stdout=res.stdout,
                stderr=res.stderr,
            )
            return res.stdout if res.returncode == 0 else f"Erro ao executar df: {res.stderr}"

        if target in ALLOWED_TARGETS:
            remote_cmd = f"pct exec {target} -- df -h"
            cmd = ["ssh", "-o", "StrictHostKeyChecking=accept-new", f"root@{PROXMOX_HOST}", remote_cmd]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
            audit.log_command_result(
                tool=tool_name,
                tool_call_id=tool_call_id,
                step=step,
                target=target,
                resolved_target=PROXMOX_HOST,
                transport="ssh",
                command=remote_cmd,
                executed_argv=cmd,
                exit_code=res.returncode,
                timed_out=False,
                duration_ms=int((time.monotonic() - started) * 1000),
                stdout=res.stdout,
                stderr=res.stderr,
            )
            return res.stdout if res.returncode == 0 else f"Erro no container {target}: {res.stderr}"

        return f"Alvo '{target}' desconhecido."
    except Exception as e:
        audit.log_command_result(
            tool=tool_name,
            tool_call_id=tool_call_id,
            step=step,
            target=target,
            resolved_target=PROXMOX_HOST if target in ALLOWED_TARGETS else target,
            transport="ssh" if target in ALLOWED_TARGETS else "local",
            command=f"pct exec {target} -- df -h" if target in ALLOWED_TARGETS else "df -h",
            exit_code=None,
            timed_out=isinstance(e, subprocess.TimeoutExpired),
            duration_ms=int((time.monotonic() - started) * 1000),
            stdout="",
            stderr="",
            error=str(e),
        )
        return f"Exceção ao consultar disco: {str(e)}"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    audit.bind_request(update)
    stage = "message_received"

    try:
        if update.effective_user.id != ALLOWED_USER_ID:
            audit.log_access_denied(user_text)
            return

        audit.log_message_received(user_text, update.message.message_id)
        await update.message.reply_text("Processando com DeepSeek...")

        stage = "llm_request"
        audit.log_llm_request(1, MODEL, 2, TOOL_NAMES)
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text}
            ],
            tools=tools,
            tool_choice="auto"
        )

        response_message = response.choices[0].message
        audit.log_llm_response(
            1,
            response_message.content,
            response.choices[0].finish_reason,
            audit.serialize_tool_calls(response_message.tool_calls),
            audit.usage_to_dict(response.usage),
            MODEL,
        )

        if response_message.tool_calls:
            stage = "tool_call"
            for tool_call in response_message.tool_calls:
                func_name = tool_call.function.name
                args = json.loads(tool_call.function.arguments or "{}")
                audit_fields = tool_audit_fields(func_name, args)
                audit.log_tool_call(
                    tool=func_name,
                    tool_call_id=tool_call.id,
                    step=1,
                    arguments=args,
                    **audit_fields,
                )

                if func_name == "run_system_update":
                    target_vmid = args.get("vmid")
                    await update.message.reply_text(f"Iniciando atualização no target: `{target_vmid}`...")
                    with audit.tool_context(
                        tool=func_name,
                        tool_call_id=tool_call.id,
                        step=1,
                        **audit_fields,
                    ):
                        result = execute_apt_update(target_vmid)
                    response_text = f"Resultado:\n```\n{result}\n```"
                    stage = "response_sent"
                    audit.log_response_sent(response_text)
                    await update.message.reply_text(response_text, parse_mode="Markdown")

                elif func_name == "check_disk_usage":
                    target = args.get("target", "local")
                    await update.message.reply_text(f"Consultando espaço em disco ({target})...")
                    with audit.tool_context(
                        tool=func_name,
                        tool_call_id=tool_call.id,
                        step=1,
                        **audit_fields,
                    ):
                        result = execute_disk_usage(target)
                    response_text = f"Uso de Disco:\n```\n{result}\n```"
                    stage = "response_sent"
                    audit.log_response_sent(response_text)
                    await update.message.reply_text(response_text, parse_mode="Markdown")
        else:
            stage = "response_sent"
            audit.log_response_sent(response_message.content or "")
            await update.message.reply_text(response_message.content)

    except Exception as e:
        audit.log_error(stage, e)
        error_text = f"Erro ao processar: {str(e)}"
        audit.log_response_sent(error_text)
        await update.message.reply_text(error_text)

    finally:
        audit.unbind_request()

if __name__ == "__main__":
    audit.setup(
        "homelab-bot",
        model=MODEL,
        system_prompt=SYSTEM_PROMPT,
        tools=TOOL_NAMES,
    )
    request_cfg = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=20.0,
        write_timeout=20.0
    )

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).request(request_cfg).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
