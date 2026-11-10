import os
import subprocess
import json
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.request import HTTPXRequest
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

# Carrega as variáveis do arquivo /opt/homelab-bot/.env
load_dotenv("/opt/homelab-bot/.env")

# ================= CONFIGURAÇÕES =================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ALLOWED_USER_ID = 6929102708  # Seu ID numérico do Telegram
PROXMOX_HOST = "192.168.30.250"

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

def execute_apt_update(vmid: str) -> str:
    """Dispara via SSH no nó do Proxmox a atualização do container via pct exec."""
    if vmid == "all":
        targets = list(ALLOWED_TARGETS.keys())
    elif vmid in ALLOWED_TARGETS:
        targets = [vmid]
    else:
        return f"Erro: ID {vmid} não está na lista de hosts permitidos."

    logs = []
    for target in targets:
        name = ALLOWED_TARGETS[target]
        remote_cmd = f"pct exec {target} -- sh -c 'DEBIAN_FRONTEND=noninteractive apt-get update && DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y'"
        cmd = ["ssh", "-o", "StrictHostKeyChecking=accept-new", f"root@{PROXMOX_HOST}", remote_cmd]

        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300)
            if res.returncode == 0:
                logs.append(f"Host {target} ({name}): Sucesso.")
            else:
                logs.append(f"Host {target} ({name}): Falha (exit code {res.returncode})\nStderr: {res.stderr.strip()}")
        except subprocess.TimeoutExpired:
            logs.append(f"Host {target} ({name}): Tempo limite esgotado (timeout 300s).")
        except Exception as e:
            logs.append(f"Host {target} ({name}): Exceção: {str(e)}")

    return "\n\n".join(logs)

def execute_disk_usage(target: str = "local") -> str:
    """Verifica espaço em disco local ou em containers remotos via SSH/pct."""
    try:
        if target == "local":
            res = subprocess.run(["df", "-h"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
            return res.stdout if res.returncode == 0 else f"Erro ao executar df: {res.stderr}"

        if target in ALLOWED_TARGETS:
            remote_cmd = f"pct exec {target} -- df -h"
            cmd = ["ssh", "-o", "StrictHostKeyChecking=accept-new", f"root@{PROXMOX_HOST}", remote_cmd]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
            return res.stdout if res.returncode == 0 else f"Erro no container {target}: {res.stderr}"

        return f"Alvo '{target}' desconhecido."
    except Exception as e:
        return f"Exceção ao consultar disco: {str(e)}"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Proteção de acesso: ignora mensagens de qualquer usuário não autorizado
    if update.effective_user.id != ALLOWED_USER_ID:
        return

    user_text = update.message.text
    await update.message.reply_text("Processando com DeepSeek...")

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": f"Você é o orquestrador do homelab Proxmox. Hosts disponíveis: {json.dumps(ALLOWED_TARGETS)}. Se o usuário solicitar atualização de sistema ou checagem de pacotes/disco, invoque as ferramentas apropriadas."
                },
                {"role": "user", "content": user_text}
            ],
            tools=tools,
            tool_choice="auto"
        )

        response_message = response.choices[0].message

        if response_message.tool_calls:
            for tool_call in response_message.tool_calls:
                func_name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)

                if func_name == "run_system_update":
                    target_vmid = args.get("vmid")
                    await update.message.reply_text(f"Iniciando atualização no target: `{target_vmid}`...")
                    result = execute_apt_update(target_vmid)
                    await update.message.reply_text(f"Resultado:\n```\n{result}\n```", parse_mode="Markdown")

                elif func_name == "check_disk_usage":
                    target = args.get("target", "local")
                    await update.message.reply_text(f"Consultando espaço em disco ({target})...")
                    result = execute_disk_usage(target)
                    await update.message.reply_text(f"Uso de Disco:\n```\n{result}\n```", parse_mode="Markdown")
        else:
            await update.message.reply_text(response_message.content)

    except Exception as e:
        await update.message.reply_text(f"Erro ao processar: {str(e)}")

if __name__ == "__main__":
    request_cfg = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=20.0,
        write_timeout=20.0
    )

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).request(request_cfg).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
