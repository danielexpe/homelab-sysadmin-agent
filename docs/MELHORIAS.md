# Melhorias Planejadas

Este documento é o índice das melhorias do Homelab SysAdmin Agent. Cada item deve ter uma especificação própria em `docs/specs/` e preservar os contratos descritos em `docs/SPEC_REGRESSAO.md`.

## 1. Ordem de prioridade

| Ordem | Grupo | Decisão |
| :---: | :--- | :--- |
| 1 | Log de auditoria local | Primeira prioridade de implementação |
| 2 | Riscos aceitos `RA-01` e `RA-02` | Não serão implementados; risco aceito pelo proprietário do projeto |
| 3 | Backlog futuro `B-01` a `B-09` | Documentados para etapas futuras, sem data definida |

Os riscos aceitos permanecem registrados para rastreabilidade e revisão futura, mas **não fazem parte do backlog de implementação**.

## 2. Log de auditoria local (P0)

**Status:** primeira prioridade; especificação pronta, implementação pendente
**Prioridade:** P0
**Especificação:** [`specs/01-log-auditoria-local.md`](specs/01-log-auditoria-local.md)

### Objetivo

Manter um arquivo de log local, persistente e legível por máquina, contendo:

- todos os prompts enviados pelos usuários autorizados;
- todas as respostas retornadas pelo LLM ao usuário;
- todos os comandos executados, incluindo alvo, resultado, código de saída e saídas padrão/erro;
- chamadas de ferramentas e erros operacionais;
- correlação entre uma mensagem recebida e todas as ações dela derivadas.

### Escopo

- Aplicar aos dois serviços: `sysadmin-agent` e `homelab-bot`.
- Armazenar em JSON Lines (`.jsonl`), um evento por linha.
- Gravar em volume Docker persistente (`./logs:/app/logs`).
- Redigir segredos e não registrar conteúdo de arquivos sensíveis (chaves, certificados, `.env`).

### Critérios de aceite resumidos

1. Uma mensagem autorizada gera um `correlation_id` e eventos de recebimento, chamadas ao LLM, ferramentas, comandos e resposta final.
2. Os comandos registrados refletem exatamente o que foi executado, incluindo `exit_code` e duração.
3. Tokens, chaves de API, senhas e blocos de chave privada nunca aparecem no arquivo.
4. O conteúdo de arquivos com extensão/padrão sensível não é registrado.
5. Falhas de escrita do log não interrompem o atendimento do bot.
6. O recurso pode ser desativado por variável de ambiente.
7. A rotação/retenção é configurável e, por padrão, nenhum evento é descartado.
8. Testes automatizados cobrem os cenários LOG-01 a LOG-15 da especificação.

## 3. Riscos aceitos (não implementar)

Os itens abaixo foram identificados como riscos críticos, mas **não serão tratados neste projeto por decisão do proprietário**. O risco é aceito e controlado em outras camadas de segurança fora deste repositório.

### RA-01 - Exposição de segredos pelo próprio agente

**Classificação:** risco crítico aceito
**Decisão:** não implementar mitigação neste projeto

**Descrição:** `load_dotenv()` carrega tokens e chaves em `os.environ`. Comandos locais executados pelo agente herdam esse ambiente, e as ferramentas `execute_ssh`, `read_file` e `write_file` permitem acesso a `.env`, chaves SSH e outros arquivos sensíveis. O conteúdo pode ser enviado ao LLM e retornado ao Telegram.

**Exemplos de exposição:** `env`, `cat /app/.env`, leitura de `/root/.ssh/id_ed25519`, impressão de variáveis em logs de aplicação.

**Controles compensatórios:** aplicados externamente, em camadas de segurança sob responsabilidade do proprietário.

**Observação:** a redação de segredos definida no log de auditoria (P0) continua obrigatória; ela protege apenas o arquivo de log e não substitui a mitigação deste risco.

**Condições para revisão:** aumento do número de usuários autorizados, exposição do bot fora da rede controlada, ampliação do escopo de chaves/credenciais acessíveis ou incidente de segurança relacionado.

### RA-02 - Execução arbitrária como root sem confirmação

**Classificação:** risco crítico aceito
**Decisão:** não implementar confirmação, allowlist ou política de comandos neste projeto

**Descrição:** o `sysadmin-agent` executa comandos shell arbitrários como `root` em hosts locais e remotos, além de permitir `pvesh` para criar, alterar e deletar recursos. Não há aprovação humana, allowlist de comandos ou separação entre ferramentas de leitura e escrita. Conteúdo malicioso presente em arquivos, logs ou saídas de comandos também pode induzir o LLM a executar ações não solicitadas (prompt injection).

**Controles compensatórios:** aplicados externamente, em camadas de segurança sob responsabilidade do proprietário.

**Condições para revisão:** uso por múltiplos operadores, aumento da autonomia do agente, acesso a ambientes de produção não isolados ou incidente operacional causado por ação não confirmada.

## 4. Backlog futuro

Itens documentados para execução em etapas futuras, sem data definida. Não devem ser implementados junto com o log de auditoria, salvo decisão explícita em contrário.

| ID | Prioridade | Item | Motivo | Status |
| :--- | :--- | :--- | :--- | :--- |
| B-01 | alta | Execução assíncrona de chamadas ao LLM e subprocessos | Chamadas síncronas bloqueiam o event loop; uma atualização de até 300s deixa o bot sem responder | futuro |
| B-02 | alta | Limite e truncamento das saídas de comando enviadas ao LLM | Evita estouro de contexto, custo elevado e ampliação da superfície de prompt injection | futuro |
| B-03 | média | Configuração de runtime do `homelab-bot` via `.env` e correção do caminho do arquivo | Remove ID, host e alvos fixos no código e alinha o código ao mount `/app/.env`; inclui validação no startup | futuro |
| B-04 | média | Testes automatizados de regressão | Garante os contratos de `SPEC_REGRESSAO.md` e os critérios LOG-01 a LOG-15 | futuro |
| B-05 | média | Memória conversacional por chat | Hoje cada mensagem perde o contexto anterior | futuro |
| B-06 | média | Fragmentação de respostas longas | Evita falha no limite de 4096 caracteres do Telegram | futuro |
| B-07 | baixa | Envio seguro de Markdown | Evita quebra de mensagens com caracteres especiais | futuro |
| B-08 | baixa | Healthcheck no Compose | Facilita operação e reinício automático | futuro |
| B-09 | baixa | Rate limiting por usuário | Protege contra uso acidental intenso e controla custo de API | futuro |

## 5. Como adicionar uma melhoria

1. Criar `docs/specs/NN-nome-curto.md` com objetivo, escopo, requisitos numerados, critérios de aceite e testes.
2. Adicionar uma linha neste índice com status, prioridade e link.
3. Verificar impacto em `docs/SPEC_REGRESSAO.md`; se houver mudança de contrato, atualizar a especificação de regressão junto com a melhoria.
4. Implementar, adicionar testes e executar a matriz de regressão.

Para um risco aceito, registrar o motivo da aceitação, os controles compensatórios externos e as condições que motivariam a revisão da decisão.
