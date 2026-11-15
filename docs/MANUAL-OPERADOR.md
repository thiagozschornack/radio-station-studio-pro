# Manual do operador

## Conceitos
- **Playout** é o serviço que toca a rádio **no servidor**. O painel apenas o comanda; fechar o navegador não tira a rádio do ar.
- **Fila de execução**: a lista editorial do que vai tocar. Ela tem prioridade sobre a rotação.
- **Rotação de contingência**: quando a fila esvazia, o playout sorteia músicas liberadas, sem repetir as últimas N execuções.
- **Mídia de contingência**: último recurso quando não há fila nem rotação possível.

## Indicador do ar (topo do painel)
| Indicador | Significado | O que fazer |
|---|---|---|
| **ON AIR** (vermelho) | áudio indo ao servidor de streaming | — |
| SEM ÁUDIO | conectado, mas sem conteúdo (silêncio) | adicione itens à fila ou libere músicas para a rotação |
| CONECTANDO | playout ativo tentando falar com o Icecast | veja “Saúde da transmissão”; confira servidor e senha |
| PLAYOUT OFFLINE | transmissão ativada, mas o serviço não responde | acione o administrador técnico |
| OFF AIR | transmissão desativada | **Iniciar transmissão** |

## Rotina
1. **Dashboard**: confira o que está no ar, o tempo restante, o próximo item e os alertas.
2. **Biblioteca → + Fila / Próxima**: “Próxima” coloca o item logo após o atual.
3. **Fila**: arraste ou use ↑ ↓ para reordenar e ✕ para remover. A fila é salva no servidor.
4. **Pular faixa**: encerra a atual (registrada como “pulada”) e toca a próxima.
5. **Parar transmissão**: desconecta os ouvintes. Peça confirmação à direção antes.

## Itens que não entram na fila
Mídias **bloqueadas**, com **licença vencida** ou **arquivadas** não podem ir ao ar. O botão fica desabilitado e o motivo aparece ao passar o mouse.

## Ainda não disponível nesta versão
Inserir vinheta instantânea, botão de emergência, entrada de microfone/estúdio ao vivo e grade horária (Fase 2). Esses botões aparecem desabilitados.
