# Especificação 01 - Log de Auditoria Local

**Status:** implementado
**Implementação:** `src/audit.py`; integração em `src/sysadmin_agent.py` e `src/bot.py`; volume `./logs:/app/logs` no Compose
**Testes:** `tests/test_audit.py` e `tests/test_agent_logging.py`

## 1. Objetivo

Registrar localmente, de forma estruturada e persistente, todo o ciclo de atendimento dos bots:

- prompts recebidos de usuários autorizados;
- requisições e respostas do LLM;
- chamadas de ferramentas;
- comandos efetivamente executados;
- resultados, erros e resposta final enviada ao usuário.

O log deve permitir responder, para qualquer atendimento: **quem pediu o quê, qual comando foi executado, onde, com qual resultado e o que foi respondido**.

## 2. Escopo

### 2.1 Incluído

- `src/sysadmin_agent.py`
- `src/bot.py`
- Volume Docker para persistência
- Documentação de configuração e operação

### 2.2 Não incluído

- Envio dos logs para serviço externo (SIEM, Loki, syslog)
- Banco de dados
- Criptografia do arquivo de log
- Assinatura ou cadeia de integridade criptográfica
- Registro de mensagens de usuários não autorizados por padrão (apenas evento de acesso negado)
- Gravação do conteúdo de arquivos sensíveis lidos por `read_file`/`cat`

## 3. Formato do arquivo

- Formato: **JSON Lines** (`.jsonl`), um objeto JSON por linha.
- Codificação: UTF-8, sem escape desnecessário (`ensure_ascii=False`).
- Nome padrão: `audit.jsonl`.
- Diretório padrão: `/app/logs`.
- No Compose, cada serviço usa um arquivo distinto: `sysadmin-agent.jsonl` e `homelab-bot.jsonl`.
- Rotação padrão: diária à meia-noite; arquivos rotacionados recebem sufixo de data (`audit.jsonl.YYYY-MM-DD`).
- Retenção padrão: `0` = manter indefinidamente.
- Cada linha deve terminar com `\n` e ser gravada com `flush` antes de considerar o evento persistido.
- O arquivo deve ser criado com permissão `0600`; o diretório, com `0700`.

## 4. Configuração

Todas as variáveis são opcionais. Valores padrão entre parênteses.

| Variável | Tipo | Padrão | Descrição |
| :--- | :--- | :--- | :--- |
| `AUDIT_LOG_ENABLED` | bool | `true` | Liga/desliga o log de auditoria |
| `AUDIT_LOG_DIR` | string | `/app/logs` | Diretório do arquivo |
| `AUDIT_LOG_FILE` | string | `audit.jsonl` | Nome do arquivo ativo |
| `AUDIT_LOG_RETENTION_DAYS` | int | `0` | Dias de retenção; `0` mantém tudo |
| `AUDIT_LOG_MAX_FIELD_CHARS` | int | `20000` | Truncamento de prompts, respostas e saídas |
| `AUDIT_LOG_CAPTURE_OUTPUT` | bool | `true` | Inclui `stdout`/`stderr` dos comandos |
| `AUDIT_LOG_CAPTURE_DENIED` | bool | `false` | Inclui o texto de mensagens de usuários não autorizados |
| `AUDIT_LOG_REDACT` | bool | `true` | Aplica redação de segredos |

Requisitos:

- **FR-01** Variáveis booleanas devem aceitar `true/false`, `1/0`, `yes/no` e `on/off`, sem diferenciar maiúsculas.
- **FR-02** Valores inválidos devem cair no padrão e emitir aviso no stderr.
- **FR-03** Com `AUDIT_LOG_ENABLED=false`, nenhum arquivo deve ser criado e o comportamento atual deve permanecer inalterado.
- **FR-04** O diretório deve ser criado automaticamente, se não existir.

## 5. Modelo de eventos

### 5.1 Campos comuns

Todo evento deve conter:

| Campo | Tipo | Descrição |
| :--- | :--- | :--- |
| `ts` | string | Timestamp ISO 8601 em UTC, com sufixo `Z` |
| `event` | string | Nome do evento |
| `service` | string | `sysadmin-agent` ou `homelab-bot` |
| `correlation_id` | string | UUID v4 único por mensagem recebida |
| `chat_id` | int/null | ID do chat do Telegram |
| `user_id` | int/null | ID do usuário do Telegram |
| `update_id` | int/null | ID da atualização do Telegram, quando disponível |

Eventos ocorridos antes de existir uma mensagem (ex.: `session_start`) usam `correlation_id` nulo.

### 5.2 Catálogo de eventos

#### `session_start`

Emitido uma vez na inicialização de cada processo.

```json
{
  "ts": "2026-11-10T12:00:00.000Z",
  "event": "session_start",
  "service": "sysadmin-agent",
  "correlation_id": null,
  "chat_id": null,
  "user_id": null,
  "update_id": null,
  "pid": 123,
  "model": "deepseek-chat",
  "system_prompt": "...",
  "system_prompt_sha256": "...",
  "tools": ["execute_ssh", "pvesh_query", "read_file", "write_file"]
}
```

#### `access_denied`

Emitido quando um usuário não autorizado envia mensagem.

```json
{
  "ts": "2026-11-10T12:00:01.000Z",
  "event": "access_denied",
  "service": "homelab-bot",
  "correlation_id": "...",
  "chat_id": 123,
  "user_id": 999,
  "update_id": 456,
  "text": null
}
```

`text` só deve ser preenchido se `AUDIT_LOG_CAPTURE_DENIED=true`.

#### `message_received`

Prompt recebido de usuário autorizado.

```json
{
  "ts": "2026-11-10T12:00:02.000Z",
  "event": "message_received",
  "service": "sysadmin-agent",
  "correlation_id": "...",
  "chat_id": 123,
  "user_id": 6929102708,
  "update_id": 457,
  "text": "Verifique o uso de disco do container 101",
  "message_id": 10
}
```

#### `llm_request`

Antes de cada chamada ao LLM.

```json
{
  "ts": "2026-11-10T12:00:02.100Z",
  "event": "llm_request",
  "service": "sysadmin-agent",
  "correlation_id": "...",
  "chat_id": 123,
  "user_id": 6929102708,
  "update_id": 457,
  "step": 1,
  "model": "deepseek-chat",
  "message_count": 2,
  "tool_names": ["execute_ssh", "pvesh_query", "read_file", "write_file"]
}
```

O conteúdo integral das mensagens não precisa ser repetido em `llm_request`; o prompt do usuário já é registrado em `message_received`. Se o sistema permitir múltiplos turnos futuramente, o conteúdo adicional deve ser registrado em eventos próprios.

#### `llm_response`

Após cada resposta do LLM.

```json
{
  "ts": "2026-11-10T12:00:03.000Z",
  "event": "llm_response",
  "service": "sysadmin-agent",
  "correlation_id": "...",
  "chat_id": 123,
  "user_id": 6929102708,
  "update_id": 457,
  "step": 1,
  "content": "",
  "finish_reason": "tool_calls",
  "tool_calls": [
    {
      "id": "call_abc",
      "name": "execute_ssh",
      "arguments": "{\"target\":\"proxmox\",\"command\":\"pct exec 101 -- df -h\"}"
    }
  ],
  "usage": {"prompt_tokens": 500, "completion_tokens": 40, "total_tokens": 540}
}
```

#### `tool_call`

Antes da execução de uma ferramenta.

```json
{
  "ts": "2026-11-10T12:00:03.100Z",
  "event": "tool_call",
  "service": "sysadmin-agent",
  "correlation_id": "...",
  "chat_id": 123,
  "user_id": 6929102708,
  "update_id": 457,
  "step": 1,
  "tool_call_id": "call_abc",
  "tool": "execute_ssh",
  "target": "proxmox",
  "resolved_target": "192.168.30.250",
  "command": "pct exec 101 -- df -h",
  "transport": "ssh",
  "arguments": {"target": "proxmox", "command": "pct exec 101 -- df -h"}
}
```

Os argumentos de `write_file` não incluem o campo `content`; em seu lugar são registrados `content_size` e `content_sha256`.

#### `command_result`

Após qualquer comando executado por `subprocess` ou SSH.

```json
{
  "ts": "2026-11-10T12:00:04.000Z",
  "event": "command_result",
  "service": "sysadmin-agent",
  "correlation_id": "...",
  "chat_id": 123,
  "user_id": 6929102708,
  "update_id": 457,
  "step": 1,
  "tool_call_id": "call_abc",
  "tool": "execute_ssh",
  "target": "proxmox",
  "resolved_target": "192.168.30.250",
  "transport": "ssh",
  "command": "pct exec 101 -- df -h",
  "executed_argv": ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "root@192.168.30.250", "pct exec 101 -- df -h"],
  "exit_code": 0,
  "timed_out": false,
  "duration_ms": 850,
  "stdout": "Filesystem ...",
  "stderr": ""
}
```

Regras:

- **FR-05** `executed_argv` deve representar exatamente o comando executado, com a mesma ordem de argumentos.
- **FR-06** Em timeout, `timed_out=true`, `exit_code=null` e `error` deve descrever o timeout.
- **FR-07** `stdout` e `stderr` devem ser truncados em `AUDIT_LOG_MAX_FIELD_CHARS`, com sufixo `...[truncated]`.
- **FR-08** Com `AUDIT_LOG_CAPTURE_OUTPUT=false`, `stdout` e `stderr` devem ser `null`.
- **FR-09** O conteúdo de arquivos sensíveis não deve aparecer em `stdout`/`stderr`, mesmo com captura habilitada (ver seção 6.3).

#### `response_sent`

Conteúdo final entregue ao usuário.

```json
{
  "ts": "2026-11-10T12:00:05.000Z",
  "event": "response_sent",
  "service": "sysadmin-agent",
  "correlation_id": "...",
  "chat_id": 123,
  "user_id": 6929102708,
  "update_id": 457,
  "text": "O container 101 possui 42% de uso...",
  "message_id": 11
}
```

#### `error`

Falha em qualquer etapa.

```json
{
  "ts": "2026-11-10T12:00:06.000Z",
  "event": "error",
  "service": "sysadmin-agent",
  "correlation_id": "...",
  "chat_id": 123,
  "user_id": 6929102708,
  "update_id": 457,
  "step": 2,
  "stage": "llm_request",
  "error_type": "APIConnectionError",
  "message": "Connection error."
}
```

Valores de `stage`: `startup`, `message_received`, `llm_request`, `llm_response`, `tool_call`, `command`, `response_sent`, `shutdown`.

## 6. Segurança e redação

### 6.1 Regras obrigatórias

- **FR-10** Tokens de bot, chaves de API, senhas, cabeçalhos `Authorization` e blocos de chave privada nunca devem ser gravados.
- **FR-11** A redação deve substituir o valor por `[REDACTED]`, preservando a chave do campo para fins de diagnóstico.
- **FR-12** A redação deve ser aplicada recursivamente a objetos e listas.
- **FR-13** O log não deve registrar variáveis de ambiente completas nem o conteúdo do arquivo `.env`.
- **FR-14** Falhas na redação devem ser tratadas como falha segura: o campo deve ser omitido, nunca gravado em claro.

### 6.2 Padrões de segredo

Devem ser redigidos, no mínimo:

- chaves no formato `sk-...`;
- tokens do Telegram no formato `\d{6,}:[A-Za-z0-9_-]{30,}`;
- blocos `-----BEGIN ... PRIVATE KEY-----` até `-----END ... PRIVATE KEY-----`;
- valores de campos cujo nome contenha `token`, `api_key`, `apikey`, `password`, `passwd`, `secret`, `authorization` ou `private_key`;
- strings `Bearer <valor>`.

### 6.3 Caminhos sensíveis

Nunca registrar o conteúdo de saída de comandos que leiam arquivos cujo caminho corresponda a:

- `*.pem`, `*.key`, `*.crt`, `*.cer`, `*.p12`, `*.pfx`;
- `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`;
- `authorized_keys`, `known_hosts` (por precaução);
- `.env` e variantes `.env.*`;
- caminhos sob `/root/.ssh/` e `/etc/ssl/private/`.

Nesses casos, `stdout` deve ser substituído por `[REDACTED: sensitive file]` e o evento `tool_call` deve indicar `sensitive_path=true`. O comando em si continua registrado para auditoria.

## 7. Requisitos funcionais por serviço

### 7.1 `sysadmin-agent`

- **FR-15** No início de `handle_message`, gerar `correlation_id` e emitir `message_received`.
- **FR-16** Emitir `access_denied` antes de retornar para usuários não autorizados.
- **FR-17** Emitir `llm_request` antes de cada `client.chat.completions.create`.
- **FR-18** Emitir `llm_response` após cada retorno do LLM, incluindo `content`, `tool_calls`, `finish_reason` e `usage`.
- **FR-19** Emitir `tool_call` antes de cada ferramenta e `command_result` para cada comando executado.
- **FR-20** `execute_ssh`, `read_file` e `write_file` devem propagar o `correlation_id` e os metadados da ferramenta para o logger.
- **FR-21** `pvesh_query` deve registrar o comando final `pvesh ...`, não apenas os argumentos recebidos do LLM.
- **FR-22** Emitir `response_sent` com o texto final editado na mensagem de status.
- **FR-23** Emitir `error` no `except` do handler e antes de responder a mensagem de erro ao usuário.

### 7.2 `homelab-bot`

- **FR-24** Aplicar os mesmos eventos de recebimento, LLM, resposta, erro e acesso negado.
- **FR-25** `run_system_update` deve emitir `command_result` por alvo, com o comando `pct exec` completo e o resultado individual.
- **FR-26** `check_disk_usage` deve emitir `command_result` tanto para `df -h` local quanto para `pct exec <vmid> -- df -h`.
- **FR-27** O `correlation_id` deve ser propagado para as funções síncronas de execução sem alterar suas assinaturas públicas, preferencialmente via `contextvars.ContextVar`.

## 8. Requisitos não funcionais

- **FR-28** O logger deve ser thread-safe e seguro para uso concorrente.
- **FR-29** Uma falha de escrita (disco cheio, permissão) deve ser registrada em stderr e **não** deve interromper o atendimento.
- **FR-30** O impacto no tempo de resposta deve ser desprezível para arquivos locais (sem chamadas de rede).
- **FR-31** O log não deve conter quebras de linha fora do JSON; todo conteúdo multilinha deve ser escapado pelo `json.dumps`.
- **FR-32** O evento deve ser gravado antes do envio da resposta correspondente ao usuário, quando aplicável, para preservar a ordem causal.
- **FR-33** O logger deve ser inicializado uma única vez por processo.

## 9. Alterações de empacotamento

- **FR-34** Criar `src/audit.py` com a implementação compartilhada.
- **FR-35** Adicionar ao `docker-compose.yml`, em ambos os serviços:

```yaml
volumes:
  - ./.env:/app/.env:ro
  - ./ssh_keys:/root/.ssh:ro
  - ./logs:/app/logs
```

- **FR-36** Adicionar `logs/` ao `.gitignore`.
- **FR-37** Documentar as variáveis no `.env.example` e no README.
- **FR-38** O `Dockerfile` não precisa copiar o diretório de logs; ele é criado em runtime.

## 10. Exemplo de arquivo

```jsonl
{"ts":"2026-11-10T12:00:02.000Z","event":"message_received","service":"sysadmin-agent","correlation_id":"7f3c...","chat_id":123,"user_id":6929102708,"update_id":457,"text":"Verifique o disco do container 101","message_id":10}
{"ts":"2026-11-10T12:00:02.100Z","event":"llm_request","service":"sysadmin-agent","correlation_id":"7f3c...","chat_id":123,"user_id":6929102708,"update_id":457,"step":1,"model":"deepseek-chat","message_count":2,"tool_names":["execute_ssh","pvesh_query","read_file","write_file"]}
{"ts":"2026-11-10T12:00:03.000Z","event":"llm_response","service":"sysadmin-agent","correlation_id":"7f3c...","chat_id":123,"user_id":6929102708,"update_id":457,"step":1,"content":"","finish_reason":"tool_calls","tool_calls":[{"id":"call_abc","name":"execute_ssh","arguments":"{\"target\":\"proxmox\",\"command\":\"pct exec 101 -- df -h\"}"}],"usage":{"prompt_tokens":500,"completion_tokens":40,"total_tokens":540}}
{"ts":"2026-11-10T12:00:03.100Z","event":"tool_call","service":"sysadmin-agent","correlation_id":"7f3c...","chat_id":123,"user_id":6929102708,"update_id":457,"step":1,"tool_call_id":"call_abc","tool":"execute_ssh","target":"proxmox","resolved_target":"192.168.30.250","command":"pct exec 101 -- df -h","transport":"ssh","arguments":{"target":"proxmox","command":"pct exec 101 -- df -h"}}
{"ts":"2026-11-10T12:00:04.000Z","event":"command_result","service":"sysadmin-agent","correlation_id":"7f3c...","chat_id":123,"user_id":6929102708,"update_id":457,"step":1,"tool_call_id":"call_abc","tool":"execute_ssh","target":"proxmox","resolved_target":"192.168.30.250","transport":"ssh","command":"pct exec 101 -- df -h","executed_argv":["ssh","-o","StrictHostKeyChecking=accept-new","-o","BatchMode=yes","-o","ConnectTimeout=10","root@192.168.30.250","pct exec 101 -- df -h"],"exit_code":0,"timed_out":false,"duration_ms":850,"stdout":"Filesystem      Size  Used Avail Use% Mounted on\n/dev/loop0       8G  3.3G  4.7G  42% /\n","stderr":""}
{"ts":"2026-11-10T12:00:05.000Z","event":"response_sent","service":"sysadmin-agent","correlation_id":"7f3c...","chat_id":123,"user_id":6929102708,"update_id":457,"text":"O container 101 está com 42% de uso de disco.","message_id":11}
```

## 11. Critérios de aceite e testes

| ID | Cenário | Resultado esperado |
| :--- | :--- | :--- |
| LOG-01 | Mensagem autorizada | Evento `message_received` com o texto exato |
| LOG-02 | Resposta do LLM | Evento `llm_response` com conteúdo e/ou `tool_calls` |
| LOG-03 | `execute_ssh` | `tool_call` + `command_result` com comando, alvo, `exit_code` e duração |
| LOG-04 | `pvesh_query` | `command_result` com o comando `pvesh` final |
| LOG-05 | `run_system_update` | Um `command_result` por alvo, com sucesso/falha individual |
| LOG-06 | `check_disk_usage` local | `command_result` com `df -h` e saída |
| LOG-07 | Usuário não autorizado | `access_denied` sem texto por padrão; nenhum evento `message_received` |
| LOG-08 | Prompt contendo `sk-...`, token ou senha | Valores substituídos por `[REDACTED]` |
| LOG-09 | Leitura de `/root/.ssh/id_ed25519` | Comando registrado, saída substituída por `[REDACTED: sensitive file]` |
| LOG-10 | Rotação e retenção | Arquivo rotacionado no dia seguinte; retenção respeitada |
| LOG-11 | Diretório sem permissão de escrita | Erro em stderr; bot continua respondendo |
| LOG-12 | Duas mensagens simultâneas | Eventos não se corrompem e cada um mantém seu `correlation_id` |
| LOG-13 | `AUDIT_LOG_ENABLED=false` | Nenhum arquivo criado; comportamento anterior preservado |
| LOG-14 | `AUDIT_LOG_CAPTURE_OUTPUT=false` | `stdout`/`stderr` nulos, demais campos presentes |
| LOG-15 | JSONL gerado | Todas as linhas são JSON válido e parseável |

## 12. Operação

- Consultar os últimos eventos:

```bash
tail -n 50 ./logs/audit.jsonl | jq .
```

- Filtrar por correlação:

```bash
jq -c 'select(.correlation_id=="<id>")' ./logs/audit.jsonl
```

- Ver apenas comandos executados:

```bash
jq -c 'select(.event=="command_result") | {ts,target,command,exit_code}' ./logs/audit.jsonl
```

- Aviso de privacidade: o arquivo contém prompts e saídas de comando e deve ser tratado como dado sensível. Restrinja o acesso ao host e nunca o versione no Git.
