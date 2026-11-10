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

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("SYSADMIN_BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))
PVE_PRIMARY_IP = os.getenv("PROXMOX_HOST", "192.168.30.250")
MODEL = "deepseek-chat"

# Mapa de apelidos conhecidos para resolução rápida pelo modelo
KNOWN_HOSTS = {
    "proxmox": {"ip": PVE_PRIMARY_IP, "user": "root", "desc": "Nó Hipervisor Proxmox VE"},
    "pve": {"ip": PVE_PRIMARY_IP, "user": "root", "desc": "Nó Hipervisor Proxmox VE"},
    "srv-bot": {"ip": "127.0.0.1", "user": "root", "desc": "Container local onde este agente roda"}
}

logging.basicConfig(level=logging.INFO)
client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

# ================= PRIMITIVAS DE EXECUÇÃO =================

def resolve_target(target: str) -> tuple[str, str]:
    """Resolve apelidos ou aceita IPs diretos da LAN."""
    target_clean = target.lower().strip()
    if target_clean in KNOWN_HOSTS:
        return KNOWN_HOSTS[target_clean]["ip"], KNOWN_HOSTS[target_clean]["user"]
    return target.strip(), "root"

def build_pvesh_command(method: str, api_path: str, data_json: str = "{}") -> str:
    command = f"pvesh {method.lower()} {api_path} --output-format json"
    if method.lower() in ["set", "create"] and data_json:
        try:
            params = json.loads(data_json)
            if isinstance(params, dict):
                for key, value in params.items():
                    command += f" --{key} {json.dumps(str(value))}"
        except Exception:
            pass
    return command

def tool_audit_fields(fn_name: str, args: dict) -> dict:
    if fn_name == "execute_ssh":
        target = args.get("target")
        command = args.get("command")
        ip, _ = resolve_target(target) if target else (None, None)
        return {
            "target": target,
            "resolved_target": ip,
            "command": command,
            "transport": "local" if ip == "127.0.0.1" else "ssh" if ip else None,
            "sensitive_path": audit.command_has_sensitive_path(command),
        }
    if fn_name == "pvesh_query":
        command = build_pvesh_command(
            args.get("method", ""),
            args.get("api_path", ""),
            args.get("data_json", "{}"),
        )
        ip, _ = resolve_target("proxmox")
        return {
            "target": "proxmox",
            "resolved_target": ip,
            "command": command,
            "transport": "ssh",
            "sensitive_path": False,
        }
    if fn_name in ("read_file", "write_file"):
        target = args.get("target")
        file_path = args.get("file_path")
        ip, _ = resolve_target(target) if target else (None, None)
        if fn_name == "read_file":
            command = f"cat {json.dumps(file_path)}"
        else:
            command = f"cat > {json.dumps(file_path)}"
        return {
            "target": target,
            "resolved_target": ip,
            "command": command,
            "transport": "local" if ip == "127.0.0.1" else "ssh" if ip else None,
            "sensitive_path": audit.is_sensitive_path(file_path),
        }
    return {}

def execute_ssh(target: str, command: str, timeout: int = 120) -> str:
    """Executa comandos shell em um host local ou remoto via SSH."""
    ip, user = resolve_target(target)
    transport = "local" if ip == "127.0.0.1" else "ssh"

    if ip == "127.0.0.1":
        cmd = ["sh", "-c", command]
    else:
        cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            f"{user}@{ip}",
            command
        ]

    tool_name = audit.get_tool_context().get("tool") or "execute_ssh"
    started = time.monotonic()
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        audit.log_command_result(
            tool=tool_name,
            target=target,
            resolved_target=ip,
            transport=transport,
            command=command,
            executed_argv=cmd,
            exit_code=res.returncode,
            timed_out=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            stdout=res.stdout.strip(),
            stderr=res.stderr.strip(),
        )
        return json.dumps({
            "target": target,
            "ip_used": ip,
            "exit_code": res.returncode,
            "stdout": res.stdout.strip(),
            "stderr": res.stderr.strip()
        }, ensure_ascii=False)
    except subprocess.TimeoutExpired:
        audit.log_command_result(
            tool=tool_name,
            target=target,
            resolved_target=ip,
            transport=transport,
            command=command,
            executed_argv=cmd,
            exit_code=None,
            timed_out=True,
            duration_ms=int((time.monotonic() - started) * 1000),
            stdout="",
            stderr="",
            error=f"Comando expirou após {timeout}s.",
        )
        return json.dumps({"target": target, "error": f"Comando expirou após {timeout}s."})
    except Exception as e:
        audit.log_command_result(
            tool=tool_name,
            target=target,
            resolved_target=ip,
            transport=transport,
            command=command,
            executed_argv=cmd,
            exit_code=None,
            timed_out=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            stdout="",
            stderr="",
            error=str(e),
        )
        return json.dumps({"target": target, "error": str(e)})

def read_file(target: str, file_path: str) -> str:
    """Lê o conteúdo de um arquivo em qualquer máquina Linux."""
    return execute_ssh(target, f"cat {json.dumps(file_path)}")

def write_file(target: str, file_path: str, content: str) -> str:
    """Escreve conteúdo em um arquivo remoto via stdin."""
    ip, user = resolve_target(target)
    transport = "local" if ip == "127.0.0.1" else "ssh"
    command = f"cat > {json.dumps(file_path)}"
    if ip == "127.0.0.1":
        cmd = ["sh", "-c", command]
    else:
        cmd = ["ssh", "-o", "StrictHostKeyChecking=accept-new", f"{user}@{ip}", command]

    tool_name = audit.get_tool_context().get("tool") or "write_file"
    started = time.monotonic()
    try:
        res = subprocess.run(cmd, input=content, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        audit.log_command_result(
            tool=tool_name,
            target=target,
            resolved_target=ip,
            transport=transport,
            command=command,
            executed_argv=cmd,
            exit_code=res.returncode,
            timed_out=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            stdout=res.stdout.strip(),
            stderr=res.stderr.strip(),
        )
        return json.dumps({"success": res.returncode == 0, "stderr": res.stderr.strip()})
    except subprocess.TimeoutExpired as e:
        audit.log_command_result(
            tool=tool_name,
            target=target,
            resolved_target=ip,
            transport=transport,
            command=command,
            executed_argv=cmd,
            exit_code=None,
            timed_out=True,
            duration_ms=int((time.monotonic() - started) * 1000),
            stdout="",
            stderr="",
            error="Comando expirou após 30s.",
        )
        return json.dumps({"error": str(e)})
    except Exception as e:
        audit.log_command_result(
            tool=tool_name,
            target=target,
            resolved_target=ip,
            transport=transport,
            command=command,
            executed_argv=cmd,
            exit_code=None,
            timed_out=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            stdout="",
            stderr="",
            error=str(e),
        )
        return json.dumps({"error": str(e)})

def pvesh_query(method: str, api_path: str, data_json: str = "{}") -> str:
    """Executa chamadas diretas à API do Proxmox VE via utilitário pvesh."""
    cmd = build_pvesh_command(method, api_path, data_json)
    return execute_ssh("proxmox", cmd)

# ================= FERRAMENTAS DO AGENTE =================

tools = [
    {
        "type": "function",
        "function": {
            "name": "execute_ssh",
            "description": "Executa comando shell (bash/sh) em uma máquina da rede local (ex: 'proxmox', '192.168.30.50') ou em containers/VMs via pct exec / qm guest exec.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Hostname, IP ou apelido cadastrado"},
                    "command": {"type": "string", "description": "Comando exato a ser executado"}
                },
                "required": ["target", "command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pvesh_query",
            "description": "Interage diretamente com a API do Proxmox VE via pvesh para listar, criar, configurar ou deletar recursos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["get", "set", "create", "delete"], "description": "Método HTTP/CLI"},
                    "api_path": {"type": "string", "description": "Caminho da API (ex: '/cluster/resources', '/nodes/pve01/status')"},
                    "data_json": {"type": "string", "description": "JSON com parâmetros chave-valor para 'set' ou 'create' (opcional)"}
                },
                "required": ["method", "api_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Lê o conteúdo completo de um arquivo em qualquer servidor Linux ou Proxmox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Host alvo"},
                    "file_path": {"type": "string", "description": "Caminho absoluto do arquivo"}
                },
                "required": ["target", "file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Grava conteúdo em um arquivo remoto. Sempre inspecione o original antes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Host alvo"},
                    "file_path": {"type": "string", "description": "Caminho absoluto do arquivo"},
                    "content": {"type": "string", "description": "Conteúdo integral a ser gravado"}
                },
                "required": ["target", "file_path", "content"]
            }
        }
    }
]

TOOL_NAMES = [tool["function"]["name"] for tool in tools]

SYSTEM_PROMPT = f"""Você é um Agente SysAdmin e Arquiteto de Infraestrutura autônomo.
Você gerencia um nó Proxmox VE ({PVE_PRIMARY_IP}) e servidores Linux na LAN.
Hosts conhecidos: {json.dumps(KNOWN_HOSTS, ensure_ascii=False)}.

Regras de Operação:
1. Autonomia com Investigação: Se o usuário pedir algo sobre o estado das máquinas, uso de hardware, logs ou configurações, investigue via pvesh_query ou execute_ssh.
2. Gerenciamento do Proxmox:
   - Para listar VMs/Containers: prefira `pvesh_query('get', '/cluster/resources')`.
   - Para comandos em containers LXC: `pct exec <vmid> -- <comando>`.
   - Para VMs: `qm guest exec <vmid> -- <comando>`.
   - Para ligar/desligar/reiniciar: `qm start/stop/reboot <vmid>` ou `pct start/stop/reboot <vmid>`.
3. Outras Máquinas Linux: Se o usuário citar um IP de rede local, use execute_ssh diretamente no IP.
4. Alterações Sensíveis: Ao alterar configurações, sempre inspecione o estado original e informe detalhadamente o que foi modificado e por quê.
5. Formatação: Seja técnico, direto e formate as saídas importantes de forma legível."""

# ================= LOOP MULTI-TURN =================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    audit.bind_request(update)
    status_msg = None
    step = 0
    stage = "message_received"

    try:
        if update.effective_user.id != ALLOWED_USER_ID:
            audit.log_access_denied(user_text)
            return

        audit.log_message_received(user_text, update.message.message_id)
        status_msg = await update.message.reply_text("Investigando...")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text}
        ]

        max_steps = 12
        completed = False

        while step < max_steps:
            step += 1
            stage = "llm_request"
            audit.log_llm_request(step, MODEL, len(messages), TOOL_NAMES)

            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=tools,
                tool_choice="auto"
            )

            msg = response.choices[0].message
            finish_reason = response.choices[0].finish_reason
            audit.log_llm_response(
                step,
                msg.content,
                finish_reason,
                audit.serialize_tool_calls(msg.tool_calls),
                audit.usage_to_dict(response.usage),
                MODEL,
            )
            messages.append(msg)

            if not msg.tool_calls:
                stage = "response_sent"
                audit.log_response_sent(msg.content or "", status_msg.message_id)
                await status_msg.edit_text(msg.content)
                completed = True
                break

            stage = "tool_call"
            for tool_call in msg.tool_calls:
                fn_name = tool_call.function.name
                args = json.loads(tool_call.function.arguments or "{}")
                audit_fields = tool_audit_fields(fn_name, args)
                audit.log_tool_call(
                    tool=fn_name,
                    tool_call_id=tool_call.id,
                    step=step,
                    arguments=args,
                    **audit_fields,
                )

                if fn_name == "pvesh_query":
                    desc = f"API Proxmox: `{args.get('method')} {args.get('api_path')}`"
                elif fn_name == "execute_ssh":
                    desc = f"SSH em `{args.get('target')}`: `{args.get('command')[:40]}...`"
                else:
                    desc = f"Executando `{fn_name}`..."
                await status_msg.edit_text(desc)

                with audit.tool_context(
                    tool=fn_name,
                    tool_call_id=tool_call.id,
                    step=step,
                    **audit_fields,
                ):
                    if fn_name == "execute_ssh":
                        out = execute_ssh(args.get("target"), args.get("command"))
                    elif fn_name == "pvesh_query":
                        out = pvesh_query(args.get("method"), args.get("api_path"), args.get("data_json", "{}"))
                    elif fn_name == "read_file":
                        out = read_file(args.get("target"), args.get("file_path"))
                    elif fn_name == "write_file":
                        out = write_file(args.get("target"), args.get("file_path"), args.get("content"))
                    else:
                        out = json.dumps({"error": "Ferramenta inexistente."})

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": out
                })

        if not completed and step >= max_steps:
            limit_text = "Limite de ações atingido antes da conclusão total."
            audit.log_response_sent(limit_text, status_msg.message_id)
            await status_msg.edit_text(limit_text)

    except Exception as e:
        audit.log_error(stage, e, step)
        error_text = f"Erro operacional do agente: {str(e)}"
        if status_msg is None:
            try:
                await update.message.reply_text(error_text)
            except Exception:
                pass
        else:
            audit.log_response_sent(error_text, status_msg.message_id)
            await status_msg.edit_text(error_text)

    finally:
        audit.unbind_request()

if __name__ == "__main__":
    audit.setup(
        "sysadmin-agent",
        model=MODEL,
        system_prompt=SYSTEM_PROMPT,
        tools=TOOL_NAMES,
    )
    req_cfg = HTTPXRequest(connect_timeout=25.0, read_timeout=25.0, write_timeout=25.0)
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).request(req_cfg).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
