# Especificação de Regressão

## 1. Objetivo

Este documento descreve o comportamento observável atual do projeto **Homelab SysAdmin Agent** e define a linha de base que deve ser preservada em qualquer alteração futura. Ele serve como contrato de regressão: se uma mudança quebrar um item marcado como obrigatório, o comportamento deve ser considerado uma regressão.

Escopo analisado:

- `src/sysadmin_agent.py` (serviço `sysadmin-agent`)
- `src/bot.py` (serviço `homelab-bot`)
- `docker-compose.yml`
- `Dockerfile`
- `.env.example` e README

Fora de escopo: conteúdo de chaves SSH, certificados digitais e segredos do arquivo `.env`.

## 2. Visão geral do sistema

O projeto entrega dois bots de Telegram independentes, empacotados na mesma imagem Docker:

| Serviço | Container | Arquivo | Modelo | Ferramentas |
| :--- | :--- | :--- | :--- | :--- |
| `sysadmin-agent` | `homelab-sysadmin-agent` | `src/sysadmin_agent.py` | `deepseek-chat` | `execute_ssh`, `pvesh_query`, `read_file`, `write_file` |
| `homelab-bot` | `homelab-bot` | `src/bot.py` | `deepseek-chat` | `run_system_update`, `check_disk_usage` |

Ambos usam `python-telegram-bot` em modo polling, `openai` com `base_url=https://api.deepseek.com` e executam comandos via `subprocess`.

## 3. Configuração e ambiente

### 3.1 Variáveis de ambiente

| Variável | Consumidor | Obrigatória | Comportamento atual |
| :--- | :--- | :---: | :--- |
| `SYSADMIN_BOT_TOKEN` | `sysadmin_agent.py` | Sim | Token do Telegram; ausência impede o polling |
| `TELEGRAM_BOT_TOKEN` | `bot.py` | Sim | Token do Telegram; ausência impede o polling |
| `DEEPSEEK_API_KEY` | ambos | Sim | Chave usada pelo cliente OpenAI |
| `ALLOWED_USER_ID` | `sysadmin_agent.py` | Sim | Convertido para `int`; padrão `0` se ausente |
| `PROXMOX_HOST` | `sysadmin_agent.py` | Sim | IP do nó Proxmox; padrão `192.168.30.250` |

O `homelab-bot` **não** lê essas variáveis de usuário do `.env`: `ALLOWED_USER_ID`, `PROXMOX_HOST` e `ALLOWED_TARGETS` estão fixos no código (`src/bot.py:17-25`).

### 3.2 Carregamento do `.env`

- `sysadmin_agent.py` chama `load_dotenv()` sem caminho (`src/sysadmin_agent.py:11`), portanto depende do diretório de trabalho (`/app` no container).
- `bot.py` chama `load_dotenv("/opt/homelab-bot/.env")` (`src/bot.py:12`), caminho que **não** corresponde ao mount atual do Compose (`/app/.env`). Esse é um comportamento conhecido e documentado no README.

### 3.3 Docker Compose

- Ambos os serviços usam `network_mode: "host"`.
- Ambos montam `./.env:/app/.env:ro` e `./ssh_keys:/root/.ssh:ro`.
- `restart: unless-stopped`.
- `PYTHONUNBUFFERED=1`.
- Imagem única construída pelo `Dockerfile` (`python:3.12-alpine` + `openssh-client`, `tzdata`, `bash`).

## 4. Contratos funcionais

### 4.1 Comum aos dois serviços

| ID | Requisito | Evidência no código |
| :--- | :--- | :--- |
| R-COM-01 | O bot deve processar apenas mensagens de texto que não sejam comandos (`filters.TEXT & ~filters.COMMAND`). | `src/sysadmin_agent.py:254`, `src/bot.py:167` |
| R-COM-02 | Mensagens de usuários não autorizados devem ser ignoradas silenciosamente, sem resposta. | `src/sysadmin_agent.py:185-186`, `src/bot.py:115-116` |
| R-COM-03 | O bot deve responder no mesmo chat da mensagem recebida. | `update.message.reply_text` |
| R-COM-04 | O processamento deve ocorrer em modo polling. | `app.run_polling()` |
| R-COM-05 | O cliente LLM deve usar o modelo `deepseek-chat`. | `src/sysadmin_agent.py:203`, `src/bot.py:123` |
| R-COM-06 | O bot deve enviar uma mensagem de status antes de iniciar o processamento. | `src/sysadmin_agent.py:189`, `src/bot.py:119` |

### 4.2 `sysadmin-agent`

#### 4.2.1 Loop do agente

| ID | Requisito |
| :--- | :--- |
| R-SYS-01 | Cada mensagem deve iniciar um contexto novo contendo o `SYSTEM_PROMPT` e a mensagem do usuário. Não há memória entre mensagens. |
| R-SYS-02 | O loop deve permitir no máximo 12 iterações de chamadas ao LLM por mensagem. |
| R-SYS-03 | Enquanto houver `tool_calls`, o agente deve executar a ferramenta correspondente e devolver o resultado ao LLM com `role: tool` e o `tool_call_id` correto. |
| R-SYS-04 | Quando o LLM retornar conteúdo sem `tool_calls`, o conteúdo deve substituir a mensagem de status. |
| R-SYS-05 | Ao atingir 12 iterações sem conclusão, deve ser exibida a mensagem `Limite de ações atingido antes da conclusão total.` |
| R-SYS-06 | Exceções durante o processamento devem ser convertidas em `Erro operacional do agente: <mensagem>`. |
| R-SYS-07 | Antes de cada ferramenta, o status deve ser atualizado com uma descrição da ação: `API Proxmox: ...`, `SSH em ...` ou `Executando ...`. |

#### 4.2.2 Resolução de alvos

| ID | Requisito |
| :--- | :--- |
| R-SYS-08 | Os apelidos `proxmox` e `pve` devem resolver para `PROXMOX_HOST` com usuário `root`. |
| R-SYS-09 | O apelido `srv-bot` deve resolver para `127.0.0.1` com usuário `root`. |
| R-SYS-10 | Qualquer outro valor deve ser tratado como host/IP literal com usuário `root`. |
| R-SYS-11 | A resolução deve ser case-insensitive e ignorar espaços nas extremidades. |

#### 4.2.3 `execute_ssh`

| ID | Requisito |
| :--- | :--- |
| R-SYS-12 | Para `127.0.0.1`, o comando deve ser executado localmente via `sh -c`. |
| R-SYS-13 | Para hosts remotos, deve ser usado `ssh` com `StrictHostKeyChecking=accept-new`, `BatchMode=yes` e `ConnectTimeout=10`. |
| R-SYS-14 | O timeout padrão deve ser de 120 segundos. |
| R-SYS-15 | A saída deve ser um JSON com `target`, `ip_used`, `exit_code`, `stdout` e `stderr`, com `ensure_ascii=False` e campos de saída sem espaços nas extremidades. |
| R-SYS-16 | Em timeout, a saída deve ser um JSON com `target` e `error` contendo `Comando expirou após <timeout>s.`. |
| R-SYS-17 | Outras exceções devem ser retornadas como JSON com `target` e `error`. |

#### 4.2.4 `read_file` e `write_file`

| ID | Requisito |
| :--- | :--- |
| R-SYS-18 | `read_file` deve executar `cat <caminho>` no alvo, com o caminho citado via `json.dumps`. |
| R-SYS-19 | `write_file` deve gravar o conteúdo via stdin em `cat > <caminho>`, com timeout de 30 segundos. |
| R-SYS-20 | `write_file` deve retornar JSON com `success` booleano e `stderr`; exceções devem retornar JSON com `error`. |

#### 4.2.5 `pvesh_query`

| ID | Requisito |
| :--- | :--- |
| R-SYS-21 | O comando base deve ser `pvesh <method> <api_path> --output-format json`, executado no apelido `proxmox`. |
| R-SYS-22 | Para `set` ou `create`, os pares de `data_json` devem ser convertidos em flags `--chave valor`, com valores citados via `json.dumps(str(v))`. |
| R-SYS-23 | JSON inválido em `data_json` deve ser ignorado sem interromper a chamada. |

#### 4.2.6 Prompt de sistema

| ID | Requisito |
| :--- | :--- |
| R-SYS-24 | O prompt deve instruir o agente a investigar o estado real antes de responder. |
| R-SYS-25 | O prompt deve orientar o uso de `pvesh_query('get', '/cluster/resources')` para inventário. |
| R-SYS-26 | O prompt deve orientar `pct exec` para LXC e `qm guest exec` para VMs. |
| R-SYS-27 | Alterações sensíveis devem ser precedidas de inspeção do estado original e acompanhadas de explicação. |

### 4.3 `homelab-bot`

| ID | Requisito |
| :--- | :--- |
| R-BOT-01 | O ID autorizado é fixo (`6929102708`). |
| R-BOT-02 | Os alvos permitidos são fixos: `100`, `101` e `102`. |
| R-BOT-03 | `run_system_update` deve aceitar um `vmid` ou `all`. |
| R-BOT-04 | Para `all`, a atualização deve iterar sobre todos os alvos permitidos. |
| R-BOT-05 | Um `vmid` fora da lista deve retornar `Erro: ID <vmid> não está na lista de hosts permitidos.` |
| R-BOT-06 | A atualização deve executar, via SSH no Proxmox, `pct exec <vmid> -- sh -c 'DEBIAN_FRONTEND=noninteractive apt-get update && DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y'`. |
| R-BOT-07 | O timeout da atualização deve ser de 300 segundos por alvo. |
| R-BOT-08 | O resultado deve indicar sucesso ou falha por alvo, incluindo `exit code` e `stderr` em caso de falha, ou timeout. |
| R-BOT-09 | `check_disk_usage` deve aceitar `local` (padrão) ou um `vmid` permitido. |
| R-BOT-10 | Para `local`, deve executar `df -h` no container do bot com timeout de 15 segundos. |
| R-BOT-11 | Para um `vmid` permitido, deve executar `pct exec <vmid> -- df -h` via SSH com timeout de 15 segundos. |
| R-BOT-12 | Alvos desconhecidos devem retornar `Alvo '<target>' desconhecido.`. |
| R-BOT-13 | As respostas de ferramentas devem ser enviadas em bloco Markdown. |
| R-BOT-14 | Quando não houver `tool_calls`, o conteúdo do LLM deve ser enviado ao usuário. |
| R-BOT-15 | Exceções devem ser respondidas como `Erro ao processar: <mensagem>`. |

## 5. Requisitos não funcionais observáveis

| ID | Requisito |
| :--- | :--- |
| R-NF-01 | O código deve executar em Python 3.12 dentro da imagem Alpine definida no `Dockerfile`. |
| R-NF-02 | As dependências devem ser instaladas exclusivamente a partir de `requirements.txt`. |
| R-NF-03 | O container não deve depender de arquivos fora dos mounts declarados no Compose. |
| R-NF-04 | Logs de execução devem ir para o stdout/stderr do container (`PYTHONUNBUFFERED=1`). |
| R-NF-05 | Segredos não devem ser versionados; `.env` e chaves privadas estão no `.gitignore`. |

## 6. Matriz de testes de regressão

Os testes abaixo devem ser executados antes e depois de qualquer alteração. Testes marcados como **manual** exigem Telegram/Proxmox reais; os demais podem ser automatizados com mocks.

### 6.1 Autorização e roteamento

| Caso | Cenário | Resultado esperado |
| :--- | :--- | :--- |
| RG-01 | Mensagem de usuário não autorizado no `sysadmin-agent` | Nenhuma resposta, nenhuma chamada ao LLM |
| RG-02 | Mensagem de usuário não autorizado no `homelab-bot` | Nenhuma resposta, nenhuma chamada ao LLM |
| RG-03 | Mensagem com `/comando` | Ignorada pelo handler |
| RG-04 | Mensagem de texto autorizada | Status inicial enviado e processamento iniciado |

### 6.2 `sysadmin-agent`

| Caso | Cenário | Resultado esperado |
| :--- | :--- | :--- |
| RG-05 | LLM responde diretamente, sem ferramenta | Status é editado com o conteúdo |
| RG-06 | LLM solicita `execute_ssh` | Comando executado e resultado devolvido ao LLM |
| RG-07 | LLM solicita `read_file` | `cat` executado no alvo |
| RG-08 | LLM solicita `write_file` | Conteúdo gravado via stdin e resultado JSON retornado |
| RG-09 | LLM solicita `pvesh_query` com `get` | `pvesh get <path> --output-format json` executado no Proxmox |
| RG-10 | LLM solicita `pvesh_query` com `create` e `data_json` | Flags `--chave valor` adicionadas ao comando |
| RG-11 | `data_json` inválido | Chamada prossegue sem os parâmetros |
| RG-12 | Ferramenta inexistente | Resultado `{"error": "Ferramenta inexistente."}` |
| RG-13 | Timeout de SSH | JSON `{"target": ..., "error": "Comando expirou após 120s."}` |
| RG-14 | 12 iterações sem conclusão | Mensagem de limite de ações |
| RG-15 | Exceção no loop | Mensagem `Erro operacional do agente: ...` |
| RG-16 | Apelidos `proxmox`, `pve`, `srv-bot` | Resolução para IP/usuário definidos |
| RG-17 | IP literal | Usuário `root` e IP preservado |
| RG-18 | Falha de SSH | JSON com `exit_code` diferente de zero e `stderr` preenchido |

### 6.3 `homelab-bot`

| Caso | Cenário | Resultado esperado |
| :--- | :--- | :--- |
| RG-19 | `run_system_update` com `vmid=100` | Um comando `pct exec` executado |
| RG-20 | `run_system_update` com `vmid=all` | Três comandos executados (100, 101, 102) |
| RG-21 | `run_system_update` com `vmid=999` | Erro de alvo não permitido |
| RG-22 | Atualização falha em um alvo | Resultado reporta falha com `exit code` e `stderr` |
| RG-23 | `check_disk_usage` com `local` | `df -h` local executado |
| RG-24 | `check_disk_usage` com `100` | `pct exec 100 -- df -h` executado |
| RG-25 | `check_disk_usage` com `999` | `Alvo '999' desconhecido.` |
| RG-26 | LLM responde sem ferramenta | Conteúdo enviado ao usuário |
| RG-27 | Exceção no processamento | Mensagem `Erro ao processar: ...` |

### 6.4 Empacotamento

| Caso | Cenário | Resultado esperado |
| :--- | :--- | :--- |
| RG-28 | `docker compose config` | Arquivo válido, dois serviços com os mounts e comandos atuais |
| RG-29 | `docker build .` | Build conclui sem erro |
| RG-30 | Subida de `homelab-bot` | Container inicia e faz polling sem erro de token |

## 7. Limitações conhecidas (não são requisitos desejáveis)

Os itens abaixo descrevem o comportamento atual. Eles só podem mudar com uma decisão explícita e atualização deste documento:

1. **Sem memória conversacional**: cada mensagem é processada isoladamente.
2. **Configuração fixa no `homelab-bot`**: ID do usuário, host e alvos exigem edição de código.
3. **Caminho de `.env` inconsistente no `homelab-bot`**: `/opt/homelab-bot/.env` não corresponde ao mount do Compose.
4. **Execução síncrona em handlers assíncronos**: chamadas ao LLM e `subprocess` bloqueiam o event loop do bot.
5. **Sem fragmentação de mensagens**: respostas acima do limite do Telegram (4096 caracteres) podem falhar.
6. **Sem confirmação para ações destrutivas**: o agente pode executar comandos arbitrários sem aprovação adicional.
7. **Sem log de auditoria persistente**: prompts, respostas e comandos só aparecem nos logs padrão do container, quando registrados.
8. **Tratamento de Markdown frágil**: saídas com caracteres especiais podem quebrar o envio com `parse_mode="Markdown"`.
9. **Sem testes automatizados** no repositório.

## 8. Critérios de aprovação da regressão

Uma alteração é considerada aprovada quando:

1. Todos os casos RG-01 a RG-30 aplicáveis passam.
2. Nenhuma variável de ambiente obrigatória muda de nome ou semântica sem atualização do README e do `.env.example`.
3. Os contratos de nome e parâmetros das ferramentas permanecem compatíveis, ou a mudança está documentada e coberta por novos testes.
4. `docker build .` e `docker compose config` continuam funcionando.
5. Segredos e chaves continuam fora do versionamento.
