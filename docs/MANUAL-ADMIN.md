# Manual administrativo

## Papéis
| Papel | Pode |
|---|---|
| Superadministrador | tudo, em todas as emissoras; cria emissoras e usuários |
| Proprietário | tudo na emissora, incluindo equipe e licenças |
| Diretor | editar emissora, stream, biblioteca e licenças; operar; relatórios; auditoria |
| Programador | enviar/editar mídias, montar fila, pular; relatórios |
| Operador | iniciar/parar transmissão, montar fila, pular |
| Locutor | ver biblioteca e fila, pular |
| Comercial | ver biblioteca e relatórios |
| Analista | relatórios |

## Criar uma emissora
*Plataforma → Emissoras*: informe o slug (endereço `/r/<slug>`), o nome e, se quiser, o e-mail do proprietário. A emissora nasce com biblioteca, fila, stream e relatórios isolados.

## Configurar a transmissão
*Transmissão*: host/porta/mountpoint do Icecast, usuário e senha de source. A senha fica **cifrada no servidor** e nunca volta ao navegador. Para trocá-la, digite a nova; deixe em branco para manter a atual.
- **Formato**: MP3 (compatível com tudo), AAC (eficiente) ou Opus/Ogg (muito eficiente; sem título ICY no stream). O Opus usa 48 kHz.
- **URL pública** deve ser HTTPS se o site for HTTPS.
- Ao salvar, o playout recarrega e reconecta sozinho.

## Biblioteca e direitos
- O upload é validado no servidor. Arquivos corrompidos ou sem áudio são recusados, e duplicatas exatas são detectadas.
- A análise em segundo plano mede loudness (LUFS), true peak, silêncio inicial/final (sugere entrada/saída) e extrai a capa.
- **Licença**: registre situação, titular, referência e vigência. “Bloqueada” impede a ida ao ar. Licença vencida também.
- Mídias que já passaram pela fila são **arquivadas** em vez de apagadas, para preservar o histórico de execução.

## Relatórios
- **Execuções**: tudo o que o playout realmente transmitiu, com duração efetiva e motivo de término. O CSV usa `;` e UTF-8 com BOM (abre direto no Excel) e traz ISRC, base para prestação de contas.
- **Conexões simultâneas**: amostras reais do Icecast a cada ~15 s. Conexões não são pessoas únicas.

## Segurança da conta
Ative **MFA** em *Minha conta*. Após 5 senhas erradas a conta fica bloqueada por 15 min. Alterar a senha encerra as outras sessões. Consulte *Auditoria* para ver quem fez o quê.
