FROM python:3.12-alpine

# Instala cliente SSH e utilitários básicos
RUN apk add --no-cache openssh-client tzdata bash

WORKDIR /app

# Instala dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código da aplicação
COPY src/ /app/src/

# Configura pasta para chave SSH com permissões restritas
RUN mkdir -p /root/.ssh && chmod 700 /root/.ssh

# Comando de inicialização
CMD ["python", "src/sysadmin_agent.py"]
