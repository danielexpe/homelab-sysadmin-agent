# Homelab SysAdmin Agent

Agente autônomo de infraestrutura para gerenciamento e monitoramento de nós Proxmox VE e servidores Linux locais via Telegram e LLM (DeepSeek / OpenAI spec).

## Recursos
- **Descoberta Dinâmica de Recursos**: Consulta automática de VMs e LXCs no cluster Proxmox via `pvesh`.
- **Detecção Polimórfica de SO**: Identificação automática de distribuições Linux (Debian, Ubuntu, Alpine, RHEL/Rocky) adaptando o gerenciador de pacotes (`apt`, `apk`, `dnf`).
- **Execução Shell Segura**: Suporte a comandos em containers (`pct exec`), VMs (`qm guest exec`) e outros hosts Linux na LAN via SSH.
- **Leitura e Escrita de Arquivos Remotos**: Inspeção e alteração de arquivos de configuração em nós remotos.
- **Docker Ready**: Empacotado para execução em container leve Alpine Linux com suporte a rede host.

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
| `SYSADMIN_BOT_TOKEN` | Sim | Token de API emitido pelo `@BotFather` no Telegram | `123456789:ABC...` |
| `DEEPSEEK_API_KEY` | Sim | Chave de API da DeepSeek | `sk-...` |
| `ALLOWED_USER_ID` | Sim | Seu ID numérico do Telegram (obtido via `@userinfobot`) | `6929102708` |
| `PROXMOX_HOST` | Sim | Endereço IP do nó Proxmox VE na rede local | `192.168.30.250` |

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

### 4. Acompanhar Logs
```bash
docker compose logs -f
```

---

## Exemplos de Interação no Telegram

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

---

## Solução de Problemas (Troubleshooting)

- **Bot não responde nada:**
  - Verifique se a variável `ALLOWED_USER_ID` no `.env` corresponde exatamente ao seu ID numérico do Telegram. Mensagens originadas de IDs não cadastrados são ignoradas de forma silenciosa para proteção da infraestrutura.
- **Erro `409 Conflict: terminated by other getUpdates request`:**
  - Ocorre quando mais de uma instância com o mesmo token de bot tenta se conectar ao Telegram simultaneamente. Verifique se serviços manuais antigos (como no OpenRC/systemd) ou containers duplicados ainda estão ativos.
- **Erro de autenticação SSH (`Permission denied`):**
  - Certifique-se de que a permissão de `./ssh_keys/id_ed25519` é `600`.
  - Confirme que a chave pública correspondente foi adicionada ao arquivo `/root/.ssh/authorized_keys` no host Proxmox e nos nós remotos.
- **QEMU Guest Agent não responde na VM:**
  - Confirme se o serviço está ativo no sistema convidado (`systemctl status qemu-guest-agent`) e se a opção está marcada nas configurações de hardware da VM no Proxmox.

---

## Segurança

- O agente executa comandos no host do Proxmox e em nós da LAN com privilégios administrativos (`root`). Nunca compartilhe o bot do Telegram com terceiros.
- Mantenha a chave privada devidamente protegida e nunca comite arquivos `.env` ou pastas `ssh_keys/` no repositório Git (já configurado no `.gitignore`).
