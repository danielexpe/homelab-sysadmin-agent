# Homelab SysAdmin Agent

Agente autônomo de infraestrutura para gerenciamento e monitoramento de nós Proxmox VE e servidores Linux locais via Telegram e LLM (DeepSeek / OpenAI spec).

O projeto executa dois serviços independentes, agrupados no mesmo `docker-compose.yml`:

- **`sysadmin-agent`** (`src/sysadmin_agent.py`): agente autônomo completo, com descoberta dinâmica de VMs/LXCs, execução de comandos arbitrários, leitura/escrita de arquivos e API do Proxmox.
- **`homelab-bot`** (`src/bot.py`): bot enxuto e restrito, com ferramentas fixas para atualização de pacotes e checagem de uso de disco.

## Recursos
- **Descoberta Dinâmica de Recursos**: Consulta automática de VMs e LXCs no cluster Proxmox via `pvesh`.
- **Detecção Polimórfica de SO**: Identificação automática de distribuições Linux (Debian, Ubuntu, Alpine, RHEL/Rocky) adaptando o gerenciador de pacotes (`apt`, `apk`, `dnf`).
- **Execução Shell Segura**: Suporte a comandos em containers (`pct exec`), VMs (`qm guest exec`) e outros hosts Linux na LAN via SSH.
- **Leitura e Escrita de Arquivos Remotos**: Inspeção e alteração de arquivos de configuração em nós remotos.
- **Ferramentas Restritas no `homelab-bot`**: Atualização de pacotes (`apt update && dist-upgrade`) e checagem de disco (`df -h`), limitadas a alvos pré-cadastrados no código.
- **Docker Ready**: Empacotado para execução em container leve Alpine Linux com suporte a rede host.
- **Compose com Múltiplos Serviços**: Um único comando sobe os dois bots, compartilhando a mesma imagem, `.env` e chaves SSH.

---

## Pré-requisitos
1. **Chave SSH Autorizada:** A chave pública correspondente à privada montada no container deve constar em `/root/.ssh/authorized_keys` no nó Proxmox e nos hosts gerenciados.
2. **QEMU Guest Agent (para VMs):** Máquinas virtuais KVM precisam ter o agente instalado e ativo (`systemctl enable --now qemu-guest-agent`) e a opção ativada nas configurações da VM no Proxmox.
3. **Docker e Docker Compose:** Instalados no host que executará o agente.

---

## Estrutura do Projeto
```text
homelab-sysadmin-agent/
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── README.md
├── ssh_keys/
│   └── README.md
└── src/
    ├── bot.py
    └── sysadmin_agent.py
```

## Como Usar

### 1. Configurar Variáveis de Ambiente
Copie o modelo de variáveis de ambiente e preencha com suas credenciais:
```bash
cp .env.example .env
nano .env
```

| Variável | Obrigatória | Descrição | Exemplo |
| :--- | :---: | :--- | :--- |
| `SYSADMIN_BOT_TOKEN` | Sim | Token do bot usado pelo `sysadmin-agent` (emitido pelo `@BotFather`) | `123456789:ABC...` |
| `TELEGRAM_BOT_TOKEN` | Sim | Token do bot usado pelo `homelab-bot` (pode ser um bot separado) | `987654321:DEF...` |
| `DEEPSEEK_API_KEY` | Sim | Chave de API da DeepSeek | `sk-...` |
| `ALLOWED_USER_ID` | Sim | Seu ID numérico do Telegram (obtido via `@userinfobot`) | `6929102708` |
| `PROXMOX_HOST` | Sim | Endereço IP do nó Proxmox VE na rede local | `192.168.30.250` |

> **Atenção:** não reutilize o mesmo token em `SYSADMIN_BOT_TOKEN` e `TELEGRAM_BOT_TOKEN` com os dois serviços ativos. Dois processos fazendo `getUpdates` com o mesmo token causam o erro `409 Conflict`.
>
> O `homelab-bot` possui `ALLOWED_USER_ID`, `PROXMOX_HOST` e a lista `ALLOWED_TARGETS` (VMs/LXCs permitidos) definidos diretamente em `src/bot.py`. Ajuste-os no código antes do deploy.

### 2. Configurar Chaves SSH
Copie a chave privada SSH autorizada no Proxmox e nos hosts da LAN para a pasta `ssh_keys/`:
```bash
cp ~/.ssh/id_ed25519 ./ssh_keys/id_ed25519
chmod 600 ./ssh_keys/id_ed25519
```

### 3. Iniciar com Docker Compose
```bash
docker compose up -d --build
```

O comando sobe os dois serviços (`homelab-bot` e `sysadmin-agent`). Para subir apenas um deles:
```bash
docker compose up -d --build sysadmin-agent
docker compose up -d --build homelab-bot
```

### 4. Acompanhar Logs
```bash
# Logs de todos os serviços
docker compose logs -f

# Logs de um serviço específico
docker compose logs -f sysadmin-agent
docker compose logs -f homelab-bot
```

---

## Exemplos de Interação no Telegram

### `sysadmin-agent` (agente completo)
- **Inventário e Diagnóstico:**
  - *"Quantas VMs e containers estão rodando no Proxmox agora e qual o consumo de RAM do nó?"*
  - *"Verifique o espaço em disco de todas as máquinas."*
- **Manutenção de Pacotes:**
  - *"Atualize o SO de todos os containers ativos."*
  - *"Atualize os pacotes do container 100 e da VM 102."*
- **Inspeção de Rede e Arquivos:**
  - *"Leia o arquivo /etc/network/interfaces do Proxmox e veja se há VLANs configuradas."*
  - *"Mostre os últimos 30 logs do syslog do container 101."*
- **Hosts Avulsos na LAN:**
  - *"Conecte via SSH no host 192.168.30.50 e veja se o serviço docker está ativo."*

### `homelab-bot` (ferramentas restritas)
- **Atualização de Pacotes:**
  - *"Atualize o container 100."*
  - *"Rode a atualização em todos os hosts permitidos."*
- **Uso de Disco:**
  - *"Qual o uso de disco local?"*
  - *"Veja o espaço em disco do container 101."*

---

## Solução de Problemas (Troubleshooting)

- **Bot não responde nada:**
  - No `sysadmin-agent`, verifique se `ALLOWED_USER_ID` no `.env` corresponde exatamente ao seu ID numérico do Telegram. Mensagens de IDs não cadastrados são ignoradas silenciosamente.
  - No `homelab-bot`, o ID autorizado está fixo em `src/bot.py`; confirme que ele foi ajustado antes do build.
  - O `homelab-bot` carrega variáveis de `/opt/homelab-bot/.env` (caminho definido em `src/bot.py`). No Compose atual o `.env` é montado em `/app/.env`; ajuste o caminho no código ou exporte as variáveis para o container caso o token não seja encontrado.
- **Erro `409 Conflict: terminated by other getUpdates request`:**
  - Ocorre quando mais de uma instância com o mesmo token de bot tenta se conectar ao Telegram simultaneamente. Verifique se `SYSADMIN_BOT_TOKEN` e `TELEGRAM_BOT_TOKEN` estão apontando para bots diferentes, se há serviços manuais antigos (OpenRC/systemd) ativos ou containers duplicados.
- **Erro de autenticação SSH (`Permission denied`):**
  - Certifique-se de que a permissão de `./ssh_keys/id_ed25519` é `600`.
  - Confirme que a chave pública correspondente foi adicionada ao arquivo `/root/.ssh/authorized_keys` no host Proxmox e nos nós remotos.
- **QEMU Guest Agent não responde na VM:**
  - Confirme se o serviço está ativo no sistema convidado (`systemctl status qemu-guest-agent`) e se a opção está marcada nas configurações de hardware da VM no Proxmox.

---

## Segurança

- O agente executa comandos no host do Proxmox e em nós da LAN com privilégios administrativos (`root`). Nunca compartilhe os bots do Telegram com terceiros.
- O `homelab-bot` expõe apenas as ferramentas `run_system_update` e `check_disk_usage`, limitadas aos IDs em `ALLOWED_TARGETS`; ainda assim, revise essa lista antes do deploy.
- Mantenha a chave privada devidamente protegida e nunca comite arquivos `.env` ou pastas `ssh_keys/` no repositório Git (já configurado no `.gitignore`).
