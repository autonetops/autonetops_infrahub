# Como escrever uma regra de negócio que vira intent

Para quem escreve: engenharia de rede. Exemplo completo, feito a partir de um documento real: [`conectividade-parceiro-externo.md`](conectividade-parceiro-externo.md).

De um documento de regra de negócio saem três coisas, geradas por máquina:

1. **Configuração** dos equipamentos.
2. **Expectativas**: as provas de que a rede faz o que foi prometido.
3. **Sinais**: o que precisa ser coletado para as provas rodarem.

Só sai o que estiver escrito de um jeito que dá para classificar e medir. Este guia mostra esse jeito.

## 1. As sete caixas

Toda frase do documento vai para uma caixa, e só uma. A pergunta de teste diz qual.

| Caixa | Pergunta de teste | Como falha | Exemplo |
|---|---|---|---|
| **Intent** | Continua verdadeira se trocarmos o fabricante ou o desenho? | Não falha sozinha; quem falha são os contratos dela. | "O parceiro troca com a empresa só o tráfego previsto, por caminho cifrado e isolado." |
| **Entrada** | Muda de uma instância para outra e alguém precisa informar? | Solução rejeitada por falta de dado. | Número da solução, ASN do parceiro, rede do túnel. |
| **Contrato** | Quem perde se quebrar? Dá para medir? | Promessa quebrada. | "Aceitamos do parceiro só os prefixos cadastrados, no máximo 100." |
| **Política** | Que mudança ou desenho ela proíbe? | Mudança rejeitada, antes de chegar à rede. | "A rede do túnel é sempre perguntada, nunca sugerida." |
| **Invariante** | Se for falso, é incidente, mesmo sem ninguém ter mudado nada? | Incidente. | "Nenhuma rota de um parceiro aparece na VRF de outro." |
| **Expectativa** | O que se observa, onde, e com que limite? | Portão reprovado ou alerta. | "Sessão IKEv2 em READY por 120 s no roteador VPN ativo." |
| **Sinal** | De onde vem a observação? | Prova `não verificável`. | `ike_sa_state`, lido de `show crypto ikev2 sa`. |

O que não entra em nenhuma caixa é **mecanismo**: comando, nome de route-map, bloco de configuração. Mecanismo é saída do compilador, não se escreve. Se um valor de mecanismo importa para outro time (um nome, um route-target, uma VLAN), ele entra como **valor derivado**, com a regra de cálculo.

Como separar política de invariante, que é a dúvida mais comum:

- A regra fala de **como alocar ou construir**? É política. "Solução nova recebe o menor octeto livre de 20 a 254."
- A regra fala de **como a rede tem de estar sempre**? É invariante. "Todos os IPs de uma solução terminam no mesmo octeto."

## 2. Ordem de escrita

1. **Escreva o resultado em uma frase.** Sem VRF, VPN, BGP ou nome de equipamento. Diga para quem é, quem é o dono e a prioridade.
2. **Liste as entradas.** Uma tabela: campo, tipo, regra de validação, exemplo. Tudo que muda de uma instância para outra.
3. **Liste os valores derivados.** Tudo que se calcula a partir das entradas, com a fórmula. Um valor é entrada ou é derivado, nunca os dois.
4. **Escreva os contratos.** Uma promessa por linha, com número e unidade.
5. **Separe políticas de invariantes** com as perguntas da seção 1.
6. **Escreva a prova de cada contrato e de cada invariante**: o que observar, em que equipamento, o limite, por quanto tempo, e se bloqueia.
7. **Diga o que acontece quando não dá para observar** e quem confirma no lugar.
8. **Liste as exceções**, cada uma com dono e prazo.

## 3. Regras de redação

Cada regra traz um antes e depois tirado do documento de conectividade com parceiro.

### Uma frase, uma caixa

Antes (RN03):

> Tudo que recebemos do parceiro é marcado com o route-target `20:199` e levado à VRF compartilhada s100. Em sentido contrário, a VRF do parceiro importa o `20:100` para receber as nossas redes. Por isso toda solução precisa do seu `vrf definition` com import e export.

Depois:

| Caixa | Texto |
|---|---|
| Contrato | Cada prefixo aceito do parceiro está instalado na VRF compartilhada. |
| Invariante | A VRF de um parceiro só recebe as redes da empresa, nunca rotas de outro parceiro. |
| Valor derivado | A VRF do parceiro importa `20:100`; rotas aceitas do parceiro saem com `20:199`. |
| Removido | "precisa do seu `vrf definition`": é mecanismo, o compilador gera. |

### Número e unidade no lugar de adjetivo

| Antes | Depois |
|---|---|
| "IPsec estabelecido e estável" | "IPsec estabelecido por 120 s seguidos" |
| "configuração salva" | "a configuração em execução é igual à salva" |

### Faixa, não valor exato

Escreva "de 1 a 100 prefixos recebidos", não "1 prefixo recebido". Um valor exato vira alarme falso no dia em que o parceiro anunciar o segundo prefixo, que o acordo permite.

### Escreva a proibição

O que é proibido precisa estar escrito, senão ninguém prova que não acontece.

| Antes | Depois |
|---|---|
| "Só anunciamos ao parceiro os dois blocos públicos." | "Para um parceiro saem só `145.245.114.0/24` e `145.245.115.0/24`. Nunca rota padrão. Nunca prefixo de outro parceiro." |

### Declare origem e destino; não declare o que se deduz

Antes (RN05):

> A regra `v<N>-in` vai de Untrust para Trust. […] A zona precisa ser conferida contra a interface real de cada perna, porque nem todas as VLANs estão na zona que o nome sugere.

A primeira frase afirma um valor e a segunda avisa que ele pode estar errado. Quem lê não sabe qual vale.

Depois:

> O firewall libera estas pernas: (origem, destino). A zona de cada perna é a da interface por onde ela entra e sai.

A zona deixa de ser digitada e passa a ser calculada. Conferida na rede, a perna "parceiro até o VIP" vai de Untrust para Untrust, coisa que o texto original não deixava ver.

### Diga qual evidência não vale

Quando existe um jeito enganoso de conferir, escreva.

> Interface de túnel "up" não é evidência: ela fica "up" sem o parceiro responder. A prova é a sessão IKEv2 em READY.

Vale também para o firewall: "o teste de política dá allow" passa se houver uma regra geral liberando tudo. A prova certa é "dá allow **e a regra que casou é a da solução**".

### Separe "falhou" de "não deu para verificar"

Antes:

> Parcial: o nível 1 está OK, mas falta algo que só outro time pode confirmar.
> Não implementada: o nível 1 falhou, ou qualquer nível falhou.

As duas condições se sobrepõem. Use os três vereditos do verificador:

| Veredito | Significa |
|---|---|
| `passou` | Foi observado e está certo. |
| `falhou` | Foi observado e está errado. |
| `não verificável` | Ninguém conseguiu observar. Nunca conta como `passou`. |

Depois: "Parcial: o nível 1 passou, nada falhou e ao menos uma prova está `não verificável`, com o nome de quem confirma."

### Exceção tem dono e prazo

"O v300 é anterior a esta regra e usa `.16`" não diz até quando. Uma exceção sem prazo vira regra paralela. Escreva quem pediu, quem aprovou e quando expira. Se a exceção é definitiva, mude a regra para que ela deixe de ser exceção (por exemplo, "a faixa vale para alocações novas").

### Use o nome exato dos objetos

O original cita a lista `privates100`; nos equipamentos ela se chama `private-s100`. A máquina procura o nome ao pé da letra. Copie o nome do equipamento ou do Infrahub, e use sempre o mesmo nome para a mesma coisa (o v300 aparece como "customer-z" em um lugar e "customer-y" em outro).

### Segredo nunca entra no texto

Chave de VPN, senha de BGP e comunidade SNMP ficam no cofre. No documento vai só a referência: `{{vault:caminho#campo}}`.

## 4. Vocabulário que já existe

Use estas palavras como estão. Qualquer outra é pedido de extensão do modelo; marque como "novo".

| Campo | Valores aceitos |
|---|---|
| Domínio do intent | `connectivity`, `routing`, `observability`, `security`, `resilience`, `capacity`, `compliance`, `lifecycle`, `operations` |
| Prioridade do intent | `critical`, `high`, `medium`, `low` |
| Tipos de contrato | roteamento (`IntentRoutingContract`), alcance (`IntentReachabilityContract`), segurança (`IntentSecurityContract`), confiabilidade (`IntentReliabilityContract`), observabilidade (`IntentObservabilityContract`), livre com objetivos (`IntentGenericContract`) |
| Tipos de invariante | sem vazamento, sem rota padrão, autorização de prefixo, sem reoriginação, redundância, capacidade presente, alcance preservado, gerência fora de banda, livre |
| Severidade do invariante | `warning` (avisa), `major` (bloqueia a mudança), `critical` (bloqueia e aciona plantão) |
| Sinais no catálogo | `bgp_session_state`, `bgp_route_count`, `policy_export_violation`, `interface_errors`, `device_reachability` |
| Limite de uma prova | operador (`gt`, `ge`, `lt`, `le`, `eq`, `ne`), valor, e por quantos segundos |

Hoje só o contrato de roteamento gera expectativas automaticamente. Para os outros, a prova escrita no documento é a especificação do que falta construir; por isso ela precisa estar completa.

## 5. Modelo em branco

```markdown
# <Nome do serviço>

## 1. Intent
> <O resultado em uma frase, sem mecanismo.>

| Campo | Valor |
|---|---|
| Nome | |
| Domínio | |
| Para quem | |
| Dono | |
| Prioridade | |

## 2. Ficha
### 2.1 Entradas
| Campo | Tipo | Regra | Exemplo |
|---|---|---|---|

### 2.2 Valores derivados
| Valor | Como é calculado |
|---|---|

## 3. Contratos
| Contrato | Promessa | Termos (número e unidade) | Quem perde se quebrar |
|---|---|---|---|

## 4. Políticas
| Política | Regra | Que mudança ela rejeita |
|---|---|---|

## 5. Invariantes
| Invariante | Condição | Severidade |
|---|---|---|

## 6. Expectativas
| ID | Deriva de | Prova | Onde se observa | Limite e tempo | Bloqueia |
|---|---|---|---|---|---|

Resultado final: como os vereditos viram "pronta", "parcial" ou "não pronta".

## 7. Sinais
| Sinal | Existe ou novo | O que mede | Fonte (comando ou API) |
|---|---|---|---|

## 8. Exceções
| O que | Quem pediu | Quem aprovou | Expira em |
|---|---|---|---|

## 9. Pendências
| # | Pergunta | O que ela trava |
|---|---|---|
```

## 6. Conferência antes de entregar

- [ ] A frase do intent não cita protocolo, fabricante nem equipamento.
- [ ] O intent tem dono e prioridade.
- [ ] Cada valor é entrada ou é derivado, e todo derivado tem fórmula.
- [ ] Todo contrato tem número e unidade.
- [ ] Toda proibição está escrita como proibição.
- [ ] Todo contrato e todo invariante tem ao menos uma prova.
- [ ] Toda prova diz onde se observa, o limite, o tempo e se bloqueia.
- [ ] Está dito o que não vale como evidência.
- [ ] Está dito o que acontece quando a prova é `não verificável` e quem confirma.
- [ ] Toda exceção tem dono e prazo.
- [ ] Os nomes de objetos foram copiados do equipamento ou do Infrahub.
- [ ] Não há comando de configuração nem segredo no texto.
- [ ] O documento foi comparado com uma instância que já existe na rede, e as diferenças estão listadas.

O último item é o que mais encontra problema. Comparar o documento de parceiro com o v300 mostrou que a regra de zonas não cobria uma das pernas, que o firewall do DC02 não tinha as regras da solução e que o roteador VPN reserva estava sem limite de prefixos e sem filtro no túnel.

## 7. O que acontece com o documento depois

1. A ficha, os contratos, as políticas e os invariantes viram objetos no Infrahub, em uma branch.
2. As políticas e os invariantes rodam como verificação na revisão. Violação `major` ou `critical` não entra.
3. Aprovada a mudança, os compiladores geram a configuração, as expectativas e a coleta dos sinais.
4. A entrega acontece em etapas, e as expectativas são o portão entre uma etapa e a próxima.
5. Depois da entrega, as mesmas expectativas ficam rodando e viram alerta quando deixam de valer.

Por isso a prova é escrita uma vez, no documento, e serve para o portão da mudança, para o alerta e para a auditoria.
