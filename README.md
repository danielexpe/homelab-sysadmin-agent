# Homelab SysAdmin Agent

Agente autônomo de infraestrutura para gerenciamento e monitoramento de nós Proxmox VE e servidores Linux locais via Telegram e LLM (DeepSeek / OpenAI spec).

## Recursos
- **Descoberta Dinâmica de Recursos**: Consulta automática de VMs e LXCs no cluster Proxmox via `pvesh`.
- **Detecção Polimórfica de SO**: Identificação automática de distribuições Linux (Debian, Ubuntu, Alpine, RHEL/Rocky) adaptando o gerenciador de pacotes (`apt`, `apk`, `dnf`).
- **Execução Shell Segura**: Suporte a comandos em containers (`pct exec`), VMs (`qm guest exec`) e outros hosts Linux na LAN via SSH.
- **Leitura e Escrita de Arquivos Remotos**: Inspeção e alteração de arquivos de configuração em nós remotos.
- **Docker Ready**: Empacotado para execução em container leve Alpine Linux com suporte a rede host.

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
