# Conectividade com parceiro externo: regra de negócio pronta para intent

- **Origem:** `Regra_de_negcio.md`, escrito pela engenharia de rede. As regras RN01 a RN11 e os cinco níveis de "pronta" continuam aqui; a coluna **Origem** de cada tabela aponta para a regra original.
- **Status:** rascunho para revisão. Nada deste documento foi carregado no Infrahub.
- **Escopo:** parceiros ligados por VPN IPsec ao par de roteadores VPN (`DC01-PE-VPN-L2-01` e `DC02-PE-VPN-L2-01`). Parceiros ligados direto aos PE-LL3 (v183, v211) ficam fora.
- **Conferido contra a rede em 2026-11-22**, só leitura: configuração dos dois roteadores VPN e dos RRs (coleta das 10:02), regras, zonas e rotas do DC02-FW (ao vivo) e o F5 LB-DC2 como está no Infrahub. O que não foi possível conferir está dito na seção 8.
- **Como escrever um documento assim:** [`como-escrever-intent.md`](como-escrever-intent.md).

## O que mudou em relação ao original

O original mistura, dentro de cada regra, coisas que o modelo de intent trata em lugares diferentes. Aqui cada frase foi para um lugar só:

| Seção | O que contém | Pergunta que responde |
|---|---|---|
| 1. Intent | O resultado, em uma frase | Para que isso existe e para quem? |
| 2. Ficha da solução | O que muda de um parceiro para outro, e o que se calcula | O que alguém precisa informar? |
| 3. Contratos | As promessas que dá para medir | Quem perde se quebrar? |
| 4. Políticas | As regras de como construir uma solução | Que mudança é proibida? |
| 5. Invariantes | O que nunca pode ser falso | Se for falso, é incidente? |
| 6. Expectativas | A prova de cada promessa (os níveis de "pronta") | O que se observa, e com que limite? |
| 7. Sinais | De onde vem cada observação | Que coleta é necessária? |

Saiu do texto tudo que é mecanismo, por exemplo "toda solução precisa do seu `vrf definition` com import e export". O `vrf definition` é gerado a partir da ficha; ninguém o escreve. Os valores que eram mecanismo e continuam importando (route-targets, nomes, VLANs) estão na seção 2.2 como valores derivados.

## 1. Intent

> O parceiro troca com a empresa somente o tráfego previsto no acordo, por um caminho cifrado, isolado dos outros parceiros e inspecionado.

| Campo | Valor |
|---|---|
| Nome | `partner-v<N>-connectivity`, um por solução (RN01) |
| Domínio | `connectivity` |
| Para quem (tenant) | o parceiro da solução |
| Dono | **a definir** (pendência 1) |
| Prioridade | **a definir**: `critical`, `high`, `medium` ou `low` (pendência 1) |

A frase não cita VPN, VRF, firewall nem F5 de propósito: ela continua verdadeira se o fabricante ou o desenho mudar. O que obriga o tráfego a passar pelo firewall e pelo F5 é uma política (seção 4).

## 2. Ficha da solução

### 2.1 Entradas: o que quem cria a solução informa

| Campo | Tipo | Regra | Origem | v300 |
|---|---|---|---|---|
| `numero` | inteiro | Único. É o N de todos os nomes. | RN01 | 300 |
| `parceiro` | nome | Um nome por parceiro, igual em todo lugar. | novo | **a confirmar**: a VRF diz "customer-z", o BGP diz "customer-y" |
| `fluxo` | `parceiro-consome`, `nos-consumimos` ou `ambos` | São os fluxos 1, 2 e 3. | RN06 | `ambos` |
| `roteamento` | `bgp` ou `estatico` | | RN02 | `bgp` |
| `asn_parceiro` | inteiro | Só com `bgp`. Diferente de 20. | RN02 | 1170 |
| `peer_vpn` | IPv4 público | Endereço do equipamento do parceiro. Não pode estar em outra solução. | RN01 | 100.120.0.1 |
| `rede_tunel` | prefixo /30 | Sempre perguntada, nunca sugerida. Não pode estar em outro túnel. | RN09 | 192.168.28.192/30 |
| `prefixos_parceiro` | lista, de 1 a 100 prefixos | Os únicos aceitos do parceiro. | RN10 | 100.64.100.1/32 |
| `servidores_internos` | lista de IPs | Membros do pool quando o parceiro consome. | RN06 | 10.10.245.200 |
| `destinos_parceiro` | lista de IPs | Membros do pool quando nós consumimos. Devem estar dentro de `prefixos_parceiro` (proposta). | RN06 | 100.64.100.1 |
| `segredo_vpn` | referência ao cofre | Nunca o valor. Formato `{{vault:caminho#campo}}`. | novo | referência |
| `ticket` | texto | A mudança que originou a solução. | novo | |

### 2.2 Valores derivados: ninguém digita

| Valor | Como é calculado | Origem |
|---|---|---|
| VRF do parceiro | `v<N>` | RN01 |
| Route distinguisher | `<Loopback0 do roteador>:<N>`, um por roteador (gerador `vrf_rd`) | modelo |
| Route-targets | A VRF do parceiro importa `20:100`. As rotas aceitas do parceiro saem com `20:199`. | RN03 |
| Octeto da solução | Menor número livre de 20 a 254 em todas as redes do fluxo | RN07, RN08 |
| VIP público | `145.245.115.<octeto>`, nome `vip-<N>-<SEQ>-public-any`, VLAN `lb-out-grp1-198` | RN06 |
| Pool e SNAT quando o parceiro consome | Pool `solution-v<N>-ext`; SNAT pool `external-snat-v<N>-int` com `10.245.146.<octeto>` | RN06 |
| VIP privado | `10.245.147.<octeto>`, nome `vip-<N>-<SEQ>-private-any`, VLAN `lb-in-grp1-197` | RN06 |
| Pool e SNAT quando nós consumimos | Pool `solution-v<N>-int`; SNAT pool `internal-snat-v<N>-out` com `145.245.114.<octeto>` | RN06 |
| `<SEQ>` | Número de ordem do VIP dentro da solução, começando em 1 (pendência 8) | RN06 |
| Regras de firewall | `v<N>-in` quando o parceiro consome, `v<N>-out` quando nós consumimos, as duas em `ambos` | RN05 |
| Zonas de cada regra | A zona da interface por onde cada perna entra e sai (seção 3.2) | RN05 |
| IPs do túnel e vizinho BGP | Primeiro endereço da /30 é o nosso, segundo é o do parceiro (como no v300; pendência 7) | RN09 |
| Equipamentos | Os dois roteadores VPN, os RRs, os firewalls e os F5 dos dois data centers (pendência 4) | novo |

Os sufixos `int` e `ext` dos nomes de pool e SNAT pool não indicam o lado do membro. São gerados assim e não se corrigem (RN06).

## 3. Contratos: o que prometemos e dá para medir

Um conjunto por solução. Todos pertencem ao intent `partner-v<N>-connectivity`.

| Contrato | Promessa | Termos | Tipo no modelo | Origem |
|---|---|---|---|---|
| `v<N>-tunnel` | Existe um caminho cifrado até o parceiro. | Sessão IKEv2 com `peer_vpn` e IPsec estabelecidos no roteador VPN ativo, estáveis por 120 s. | Contrato livre com objetivos (lacuna M-1) | RN01, nível 1 |
| `v<N>-routing` | Trocamos com o parceiro só as rotas previstas. | `asn_parceiro`; IPv4 na VRF `v<N>`; aceitos só `prefixos_parceiro`; no máximo 100 prefixos; rotas aceitas marcadas `20:199`. Com `estatico`: rota para cada item de `prefixos_parceiro` pelo túnel. | `IntentRoutingContract`, papel `customer_edge` | RN02, RN03, RN10 |
| `v<N>-shared-reach` | As rotas do parceiro chegam à VRF compartilhada e as nossas chegam à VRF dele. | Cada item de `prefixos_parceiro` instalado na `s100` dos RRs com `20:199`. Os dois blocos públicos presentes na `v<N>`. | `IntentReachabilityContract` | RN03, RN04, nível 2 |
| `v<N>-tunnel-filter` | Pelo túnel só passa o tráfego previsto. | Entrada e saída: BGP com o IP do túnel; `prefixos_parceiro` com os dois blocos públicos; todo o resto negado. | `IntentSecurityContract` | RN10 |
| `v<N>-firewall` | O firewall libera as pernas da solução e nada além delas. | As pernas da seção 3.2, cada uma liberada pela regra da solução. | `IntentSecurityContract` (lacuna M-2) | RN05 |
| `v<N>-lb-service` | O serviço é entregue pelo F5 no fluxo escolhido. | VIP, pool e SNAT pool da seção 2.2; pool com os membros da ficha; VIP ativo na VLAN do fluxo. | Objetos `ServiceVIP`, `LoadbalancerPool`, `LoadbalancerSnatPool` | RN06, RN07 |
| `v<N>-signals` | Cada promessa acima tem evidência recente. | Os sinais da seção 7. | `IntentObservabilityContract` | níveis 1 a 5 |

### 3.1 O que anunciamos e o que aceitamos

| Sentido | Conjunto permitido | Limite |
|---|---|---|
| Do parceiro para nós | `prefixos_parceiro` da ficha | 100 prefixos por parceiro. Os RRs não têm limite. |
| De nós para o parceiro | `145.245.114.0/24` e `145.245.115.0/24`, sempre os mesmos | 2 prefixos |

### 3.2 Pernas de cada fluxo no firewall

O original dizia "`v<N>-in` vai de Untrust para Trust" e, logo depois, que a zona precisa ser conferida na interface. As duas frases viram uma regra só: **o contrato declara origem e destino de cada perna; a zona é derivada da interface por onde a perna passa.**

Nos dois firewalls as interfaces estão assim: `vlan.100` (lado dos parceiros, VRF `s100`), `vlan.196`, `vlan.197` e `vlan.198` na zona Untrust; `vlan.601` (lado interno) na zona Trust. A VLAN `lb-in-grp1-197` tem "in" no nome e está em Untrust.

| Fluxo | Regra | Perna | Origem | Destino | Entra por | Sai por | Zonas derivadas |
|---|---|---|---|---|---|---|---|
| Parceiro consome | `v<N>-in` | até o VIP | `prefixos_parceiro` | VIP público | `vlan.100` | `vlan.198` | Untrust para Untrust |
| Parceiro consome | `v<N>-in` | do SNAT ao servidor | SNAT `10.245.146.<octeto>` | `servidores_internos` | `vlan.197` | `vlan.601` | Untrust para Trust |
| Nós consumimos | `v<N>-out` | até o VIP | clientes internos | VIP privado | `vlan.601` | `vlan.197` | Trust para Untrust |
| Nós consumimos | `v<N>-out` | do SNAT ao parceiro | SNAT `145.245.114.<octeto>` | `destinos_parceiro` | `vlan.198` | `vlan.100` | Untrust para Untrust |

Em cada fluxo só uma das duas pernas cruza de Untrust para Trust ou o contrário. Uma regra escrita apenas com o par de zonas do original cobre uma perna e deixa a outra de fora (pendência 2).

## 4. Políticas: como uma solução pode ser construída

Uma política falha como mudança rejeitada, antes de chegar à rede.

| Política | Regra | Onde age | Origem |
|---|---|---|---|
| `partner-one-solution` | Cada solução tem um número, uma VRF e um túnel, e nenhum deles é compartilhado. | revisão | RN01 |
| `partner-traffic-inspected` | O tráfego de toda solução passa pelo firewall e pelo F5, em um dos três fluxos. | revisão | objetivo, RN06 |
| `partner-names-derived` | Nomes de VRF, VIP, pool, SNAT pool e regra de firewall seguem a seção 2.2 e não são digitados nem "corrigidos". | revisão | RN05, RN06 |
| `partner-octet-allocation` | Solução nova recebe o menor octeto livre de 20 a 254. Octeto liberado só é reutilizado se estiver livre em todas as redes do fluxo: duas em `parceiro-consome` ou `nos-consumimos`, quatro em `ambos`. | revisão | RN08 |
| `tunnel-subnet-asked` | A `rede_tunel` é sempre informada por uma pessoa depois de alinhada com o parceiro. Nem pessoa nem automação sugere um valor. | revisão | RN09 |
| `ebgp-max-prefix-required` | Toda sessão com parceiro tem limite de prefixos. Padrão 100. Já existe no Infrahub. | revisão | RN10 |
| `tunnel-acl-required` | Todo túnel de parceiro tem filtro de entrada e de saída. | revisão | RN10 |
| `partner-change-isolated` | A mudança de uma solução só altera objetos da própria solução. Sessões com os RRs, OSPF, LDP, HSRP e as outras soluções não aparecem no plano. | plano | RN11 |
| `secrets-in-vault` | Chaves ficam no cofre. No Infrahub e nos documentos só a referência. | revisão | novo |

A exceção do v300 (octeto `.16`) não quebra `partner-octet-allocation`: a política trata de como alocar daqui para frente, e o v300 foi alocado antes. No modelo o v300 fica com o octeto fixado, e a faixa vale para as alocações novas (pendência 11).

## 5. Invariantes: o que nunca pode ser falso

Um invariante falha como incidente, com ou sem mudança. As severidades são proposta.

| Invariante | Condição | Severidade | Tipo no modelo | Origem |
|---|---|---|---|---|
| `partner-isolation` | A VRF de um parceiro só importa `20:100`. Nenhuma rota de um parceiro aparece na VRF de outro. | `critical` | `IntentNoLeakInvariant` | objetivo, RN03 |
| `partner-prefix-authorization` | Todo prefixo aceito de um parceiro está em `prefixos_parceiro`. | `critical` | `IntentPrefixAuthorizationInvariant` | RN10 |
| `partner-export-public-only` | Para um parceiro só saem `145.245.114.0/24` e `145.245.115.0/24`. Nunca rota padrão. | `critical` | `IntentNoDefaultOriginationInvariant` mais um invariante livre | RN10 |
| `partner-private-not-shared` | Prefixo privado (RFC1918 e reservados) recebido de parceiro só ganha `20:199` se estiver na lista `private-s100`. | `major` | `IntentNoLeakInvariant` | RN10 |
| `solution-same-octet` | Todos os VIPs e SNATs de uma solução terminam no mesmo octeto. | `major` | invariante livre | RN07 |
| `solution-ip-unique` | Cada IP de VIP ou SNAT pertence a uma única solução, no F5 e no IPAM. | `major` | invariante livre | RN08, nível 4 |
| `tunnel-subnet-unique` | Nenhuma `rede_tunel` nem `peer_vpn` é usado por duas soluções. | `major` | invariante livre | RN09 |
| `core-sessions-preserved` | Cada roteador VPN mantém 2 sessões estabelecidas com os RRs do seu data center. | `critical` | pertence ao intent do núcleo (`bootcamp-core-transport`) | RN04, RN11 |

A RN03 original dizia "tudo que recebemos do parceiro é marcado com `20:199`", e a RN10 abria a exceção dos privados. Valem as duas linhas `partner-prefix-authorization` e `partner-private-not-shared` acima: marca-se o que foi aceito, menos os privados fora de `private-s100`.

## 6. Expectativas: quando a solução está pronta

Cada linha é uma prova que roda em três momentos: depois da mudança (portão), o tempo todo (alerta) e em auditoria. O verificador devolve `passou`, `falhou` ou `não verificável`.

| ID | Nível | Prova | Onde se observa | Sinal | Bloqueia |
|---|---|---|---|---|---|
| E1.1 | 1. VPN no ar | Sessão IKEv2 com `peer_vpn` em READY por 120 s seguidos. | roteador VPN ativo | `ike_sa_state` | sim |
| E1.2 | 1 | IPsec estabelecido com `peer_vpn` por 120 s seguidos. | roteador VPN ativo | `ipsec_sa_state` | sim |
| E2.1 | 2. Roteamento | Com `bgp`: sessão com o vizinho do túnel em Established na VRF `v<N>`. Com `estatico`: rota ativa para cada prefixo do parceiro. | roteador VPN ativo | `bgp_session_state` ou `route_presence` | sim |
| E2.2 | 2 | Prefixos recebidos: de 1 a 100, todos dentro de `prefixos_parceiro`. | roteador VPN ativo | `bgp_route_count` | sim |
| E2.3 | 2 | Prefixos anunciados ao parceiro: no máximo 2, só os blocos públicos. | roteador VPN ativo | `policy_export_violation` | sim |
| E2.4 | 2 | Cada prefixo do parceiro está na `s100` com `20:199` e instalado. | RRs | `route_presence` | sim |
| E3.1 | 3. Firewall | Existem as regras do fluxo (`v<N>-in`, `v<N>-out`), habilitadas. | firewalls | `fw_rule_present` | sim |
| E3.2 | 3 | O teste de política de cada perna da seção 3.2 dá "allow" **e a regra que casou é a da solução**. | firewalls | `fw_policy_match` | sim |
| E3.3 | 3 | Há rota para a rede do VIP e a rede do SNAT do fluxo. | firewalls | `fw_route_present` | sim |
| E4.1 | 4. F5 | VIP, pool e SNAT pool do fluxo existem com os nomes derivados; o VIP está ativo na VLAN do fluxo; o pool tem os membros da ficha. | F5 | `lb_object_state` | sim |
| E4.2 | 4 | Todos os IPs de VIP e SNAT da solução têm o mesmo octeto. | F5 | `lb_object_state` | sim |
| E4.3 | 4 | Nenhum outro objeto do F5 usa um IP da solução. | F5 | `lb_object_state` | sim |
| E4.4 | 4 | A configuração em execução está salva. | F5 | `lb_config_saved` | sim |
| E5.1 | 5. Tráfego | Teste de ponta a ponta com o IP de origem esperado depois do SNAT. | ponta a ponta | a definir (pendência 10) | não, fase posterior |

Três cuidados que o original já pedia e agora estão na prova:

- **Interface de túnel "up" não é evidência.** Ela fica "up" sem o parceiro responder. Por isso E1.1 usa a sessão IKEv2.
- **No roteador VPN reserva nada disso é esperado.** O par trabalha ativo e reserva: no reserva o túnel não sobe e a sessão BGP fica Idle. As provas E1 e E2 valem no roteador ativo do HSRP.
- **"Allow" sozinho não prova a regra.** Se existir uma regra geral que libera tudo, o teste dá "allow" sem a regra da solução. Por isso E3.2 exige o nome da regra que casou.

### Resultado final

| Resultado | Condição |
|---|---|
| **Implementada** | E1 a E4 `passou`. |
| **Parcial** | E1 `passou`, nenhuma prova `falhou` e ao menos uma está `não verificável`, com o nome de quem confirma. |
| **Não implementada** | Qualquer prova de E1 a E4 `falhou`. |

O original tinha "Parcial: falta algo que só outro time pode confirmar" e "Não implementada: qualquer nível falhou", que se sobrepunham. A diferença agora é entre `falhou` (foi observado e está errado) e `não verificável` (ninguém conseguiu observar). Uma prova `não verificável` nunca conta como `passou`.

A RN08 dizia que fora do F5 "a confirmação é do operador". Com o octeto saindo de um pool no Infrahub, o IPAM passa a ser verificável. O que estiver fora do Infrahub continua `não verificável` e pede a confirmação registrada: quem confirmou e quando.

## 7. Sinais: de onde vem cada observação

| Sinal | Situação | O que mede | Fonte |
|---|---|---|---|
| `bgp_session_state` | existe no catálogo | Sessão Established ou não, por vizinho e VRF | CLI do roteador VPN |
| `bgp_route_count` | existe | Prefixos recebidos por vizinho | CLI do roteador VPN |
| `policy_export_violation` | existe | Prefixos anunciados por vizinho (contagem) | CLI do roteador VPN |
| `ike_sa_state` | novo | Sessão IKEv2 em READY, por peer | `show crypto ikev2 sa` |
| `ipsec_sa_state` | novo | IPsec estabelecido e contadores andando, por peer | `show crypto ipsec sa peer` |
| `route_presence` | novo | Prefixo presente em uma VRF, com o route-target esperado | `show bgp vpnv4 unicast vrf` |
| `fhrp_state` | novo | Qual roteador VPN está ativo no HSRP | `show standby brief` |
| `fw_rule_present` | novo | Regra existe e está habilitada | API do PAN-OS |
| `fw_policy_match` | novo | Resultado do teste de política e nome da regra que casou | API do PAN-OS |
| `fw_route_present` | novo | Rota presente na tabela do firewall | API do PAN-OS |
| `lb_object_state` | novo | VIP, pool, membros e SNAT pool, com estado e endereço | iControl REST do F5 |
| `lb_config_saved` | novo | Configuração em execução igual à salva | a definir |

Os sinais novos precisam de entrada em `transforms/signal_catalog.py` e de um coletor. A rede de gerência bloqueia UDP (sem SNMP, syslog ou NetFlow), então toda coleta é por CLI ou API.

## 8. Como o v300 está hoje em relação a estas regras

| Item | Observado em 2026-11-22 | Situação |
|---|---|---|
| VRF `v300`, importa `20:100`, exporta com `20:199` | Presente nos dois roteadores VPN. | de acordo |
| Túnel (E1.1) | IKEv2 READY no DC01. No DC02 o túnel não sobe. | de acordo, o DC02 é o reserva |
| BGP (E2.1, E2.2) | Established no DC01 com 1 prefixo, `100.64.100.1/32`. Idle no DC02. | de acordo |
| Limite de 100 prefixos (RN10) | Configurado no DC01. **Ausente no DC02.** | falha no DC02 |
| Filtro no túnel (RN10) | Aplicado na entrada e na saída no DC01. **No DC02 os filtros existem e não estão aplicados ao túnel.** | falha no DC02 |
| Regras de firewall (E3.1, E3.2) | O DC02-FW tem só `allow-all` (qualquer origem para qualquer destino) e `Allow-Ping`. **Não existe `v300-in` nem `v300-out`.** | falha no DC02-FW |
| Rotas no firewall (E3.3) | O DC02-FW tem rota para `145.245.114.0/24`, `145.245.115.0/24` e `10.245.146.0/23`. | de acordo |
| F5, fluxo `ambos` (E4.1, E4.2) | No LB-DC2: `vip-300-1-public-any` 145.245.115.16, `vip-300-1-private-any` 10.245.147.16, pools `solution-v300-ext` e `solution-v300-int`, SNATs 10.245.146.16 e 145.245.114.16. Octeto 16 nos quatro. | de acordo |
| IPs únicos (E4.3) | O SNAT pool `snat-pool-198` também usa 145.245.114.16. | a esclarecer (pendência 6) |
| Route distinguisher | O DC02 usa `10.245.245.246:300`, que é o loopback do roteador do DC01. | diverge do modelo |

Pelo critério do próprio documento o v300 não estaria "Implementada": o nível 3 não passa no DC02-FW. Na coleta das 10:02 o caminho pelo firewall do DC01 estava fora (rastreamento dos RRs do DC01 em Down), então o tráfego seguia pelo DC02.

Não conferido: o DC01-FW ao vivo (não respondeu daqui; as zonas dele vêm do Infrahub, carga de 2026-10-26), o LB-DC1 (não está no Infrahub), o F5 ao vivo (usei o estado do Infrahub, conferido contra o equipamento em 2026-11-21) e o nível 5.

## 9. Pendências para a engenharia de rede

| # | Pergunta | O que ela trava |
|---|---|---|
| 1 | Quem é o dono de cada solução e qual a prioridade? Qual é o nome do parceiro do v300: "customer-z" ou "customer-y"? | intent, tenant |
| 2 | A tabela de pernas da seção 3.2 está certa? A regra `v<N>-in` deve ser uma regra só com os dois pares de zonas, ou uma por perna? | contrato `v<N>-firewall`, E3.2 |
| 3 | A regra `allow-all` do DC02-FW é temporária? Enquanto existir, qualquer solução "passa" no firewall sem regra própria. | nível 3 |
| 4 | Toda solução existe sempre nos dois data centers, com o roteador VPN reserva configurado igual ao ativo? O LB-DC1 entra? | lista de equipamentos |
| 5 | O original cita a lista `privates100`. Nos roteadores ela se chama `private-s100`. Qual é o nome certo? | invariante `partner-private-not-shared` |
| 6 | "IPs únicos" quer dizer um IP por solução ou um IP por objeto? O `snat-pool-198` deve existir? | E4.3 |
| 7 | Na `rede_tunel`, o primeiro endereço é sempre o nosso? | valores derivados |
| 8 | O VIP é sempre `any` (qualquer protocolo e porta)? Quando uma solução tem mais de um VIP, o que diferencia o `<SEQ>`? | contrato `v<N>-lb-service` |
| 9 | Quando o roteamento `estatico` é permitido? O que faz o papel do limite de prefixos nesse caso? | contrato `v<N>-routing` |
| 10 | Só o nível 1 tem tempo de estabilidade (2 minutos). Os níveis 2 a 4 também precisam? Quem executa o teste do nível 5 e de onde? | expectativas |
| 11 | O v300 fica no octeto `.16` para sempre, ou será renumerado? Com data, vira uma dispensa com prazo. | política de octeto |

## 10. Lacunas do modelo que este documento expõe

| ID | Lacuna | Proposta |
|---|---|---|
| M-1 | Não há contrato tipado para túnel IPsec nem objeto para o peer do parceiro. | Começar com contrato livre e objetivos; depois um tipo `IntentIPsecAttachmentContract`. |
| M-2 | `IntentSecurityContract` não tem papel "firewall" e a regra não tem origem por prefixo, então não descreve pernas. | Estender a regra com origem, destino e equipamento. |
| M-3 | O catálogo tem 5 sinais; este documento pede mais 9. | Uma entrada no catálogo e um coletor por sinal. |
| M-4 | Só o `IntentRoutingContract` gera expectativas hoje. | Compiladores para túnel, firewall e F5. |
| M-5 | O IP do túnel é o mesmo nos dois roteadores VPN, e o Infrahub exige endereço único. | Modelar o IP uma vez, ligado ao par, e renderizar nos dois. |
| M-6 | Não existe alocação que garanta o mesmo octeto em quatro redes. | Pool numérico de 20 a 254 para o octeto; os quatro IPs derivam dele. |
| M-7 | O renderizador de IOS XE não gera VRF de parceiro, túnel nem filtro do túnel. | O procedimento do v300 é a especificação. |
