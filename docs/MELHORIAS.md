# Melhorias Planejadas

Este documento é o índice das melhorias do Homelab SysAdmin Agent. Cada item deve ter uma especificação própria em `docs/specs/` e preservar os contratos descritos em `docs/SPEC_REGRESSAO.md`.

## 1. Log de auditoria local

**Status:** especificado, não implementado
**Prioridade:** alta
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
7. A rotação/retensão é configurável e, por padrão, nenhum evento é descartado.
8. Testes automatizados cobrem os cenários LOG-01 a LOG-12 da especificação.

## 2. Backlog candidato

Itens ainda não solicitados formalmente. Servem como referência para priorização e não devem ser implementados sem aprovação.

| Prioridade | Item | Motivo |
| :--- | :--- | :--- |
| alta | Memória conversacional por chat | Hoje cada mensagem perde o contexto anterior |
| alta | Execução assíncrona de LLM e subprocessos | Evita bloqueio do event loop do Telegram |
| alta | Confirmação para comandos destrutivos | Reduz risco de ações irreversíveis por engano |
| média | Fragmentação de respostas longas | Evita falha no limite de 4096 caracteres do Telegram |
| média | Configuração do `homelab-bot` via `.env` | Remove ID, host e alvos fixos no código |
| média | Correção do caminho do `.env` no `homelab-bot` | Alinha o código ao mount `/app/.env` do Compose |
| média | Testes automatizados de regressão | Garante os contratos de `SPEC_REGRESSAO.md` |
| baixa | Healthcheck no Compose | Facilita operação e reinício automático |
| baixa | Envio seguro de Markdown | Evita quebra de mensagens com caracteres especiais |
| baixa | Rate limiting por usuário | Protege contra uso acidental intenso |

## 3. Como adicionar uma melhoria

1. Criar `docs/specs/NN-nome-curto.md` com objetivo, escopo, requisitos numerados, critérios de aceite e testes.
2. Adicionar uma linha neste índice com status, prioridade e link.
3. Verificar impacto em `docs/SPEC_REGRESSAO.md`; se houver mudança de contrato, atualizar a especificação de regressão junto com a melhoria.
4. Implementar, adicionar testes e executar a matriz de regressão.
