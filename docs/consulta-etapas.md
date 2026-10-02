# Consulta de saúde dos clientes — etapas

Este documento descreve o que o script faz, na ordem em que cada etapa acontece, desde a abertura até o CSV e o dashboard. O ponto de entrada é `consultar.py`, que chama `catalyst_center.consultar.main`.

A consulta lê clientes de **Assurance › Health › Client** no Cisco Catalyst Center, aplica os filtros informados e grava dois arquivos em `relatorios/`. A senha é pedida na hora e não entra no CSV, no HTML nem em arquivo de configuração.

Há duas formas de usar o mesmo fluxo:

| Forma | Como abrir | O que muda |
| --- | --- | --- |
| Terminal | `python consultar.py` | Perguntas no terminal. Período em atalhos de 1, 3, 6, 12 ou 24 horas. Faixa de conexão dos reincidentes fica em 1–7. |
| Página local | `python pagina.py` ou `python consultar.py --pagina` | Formulário em `http://127.0.0.1:8780/`. Período com data e hora. Faixa de conexão escolhida de 1 a 10. |

A página e o terminal chamam a mesma função `run_consultation`.

## 1. Coleta dos parâmetros

No terminal, a ordem das perguntas é esta:

1. **Host.** Enter usa `https://dnac.boticario.net`. Um host sem `http://` ou `https://` recebe `https://`.
2. **Usuário e senha.** A senha é lida sem aparecer na tela. Os dois campos são obrigatórios.
3. **Tipo de cliente.** `todos` (padrão), `cabeado` (`Wired`) ou `sem fio` (`Wireless`).
4. **SSID.** Só quando o tipo não é cabeado. Aceita vários nomes separados por vírgula. `*` no nome funciona como curinga. A comparação ignora maiúsculas e minúsculas.
5. **Localidade.** Trecho do nome ou da hierarquia do site. Enter consulta todas. Nesse caso o script pede confirmação, porque a consulta pode demorar.
6. **Período.** Opções de 1, 3, 6, 12 ou 24 horas, contadas para trás a partir de agora. Enter usa 24 horas. O fim é o instante da consulta.
7. **Modo.** `1` lista todos os clientes do período. `2` lista reincidentes com conectividade baixa. No modo 2, o mínimo de ocorrências padrão é 3.

Na página, os mesmos dados vêm do formulário `Consulta.html`:

- Host, usuário e senha são obrigatórios.
- SSID é obrigatório para “todos” e “sem fio”. No tipo cabeado, o SSID é ignorado.
- Localidade vazia consulta todas, sem pergunta extra.
- Início e fim são data e hora. O fim precisa ser depois do início e não pode estar no futuro.
- “Reincidentes” mostra a faixa **Conexão de / até** (padrão 1 a 7) e o mínimo de ocorrências (padrão 3).

A verificação de certificado TLS fica desligada nesta consulta, porque o Catalyst Center costuma usar certificado interno. Os avisos de certificado são silenciados. O tempo limite de cada chamada HTTP é 120 segundos.

## 2. Autenticação

O cliente HTTP envia `POST /dna/system/api/v1/auth/token` com usuário e senha. A resposta precisa trazer o campo `Token`.

Nas consultas seguintes o token vai no cabeçalho `X-Auth-Token`. Ele é renovado depois de 50 minutos. Se uma chamada voltar HTTP 401, o script autentica de novo e repete essa chamada uma vez.

No terminal, usuário ou senha recusados permitem tentar outro par. Na página, a recusa volta como erro e o formulário permanece aberto. Falha de rede ou outro erro da API encerra a tentativa.

## 3. Resolução da localidade

Se o usuário informou uma localidade, o script lista os sites em `GET /dna/intent/api/v1/site` e procura o texto no nome e na hierarquia (`siteNameHierarchy`, `groupNameHierarchy` ou `siteHierarchy`).

O resultado são duas listas:

- **Hierarquias.** Caminhos que contêm o texto. Um site que já está abaixo de outro resultado é descartado, para não consultar a mesma árvore duas vezes.
- **IDs.** Identificador do site encontrado e dos sites abaixo dele.

Essas listas são reutilizadas em cada intervalo da consulta de reincidentes, para não listar os sites de novo.

Se a lista de sites falhar, ou se nenhum site contiver o texto, a consulta segue usando o texto informado direto na API.

Localidade vazia não restringe o escopo: a API é chamada para todas as localidades.

## 4. Busca dos clientes

A lista individual vem de `GET /dna/data/api/v1/clients`. Os parâmetros possíveis são hierarquia do site, tipo, SSID, IDs de site, visão `Wireless`, e início e fim em milissegundos desde a época Unix.

A paginação usa `limit` 500 e `offset` começando em 1. Ela para quando a página volta vazia, menor que o pedido, ou igual à página anterior. A cada página o terminal mostra quantos registros já chegaram.

### Tentativas de filtro

A API nem sempre aceita SSID e tipo juntos. Para cada hierarquia encontrada, o script tenta nesta ordem e fica com a primeira tentativa que devolver clientes dentro do escopo:

1. Com SSID e com tipo, quando a consulta é sem fio e há SSID.
2. Com tipo e sem SSID.
3. Sem tipo e sem SSID.

Se ainda não houver clientes e existirem IDs de site, há uma última tentativa: os IDs vão em blocos de 25, com o tipo informado. Os blocos são unidos e clientes repetidos pelo MAC são removidos.

Depois da resposta, o script aplica de novo, localmente:

- **Localidade**, se o campo de hierarquia veio preenchido. Se a API não trouxe localidade, as linhas permanecem, porque o filtro já foi na chamada.
- **Tipo**, comparando com `Wired` ou `Wireless`.
- **SSID**, só em clientes sem fio. No modo “todos”, os cabeados permanecem. Se a API não informou SSID, o filtro não consegue conferir a rede e uma nota explica isso.

Notas desse tipo aparecem no terminal e no subtítulo do dashboard. Exemplos: filtro de SSID aplicado só localmente, nenhum cliente na localidade, ou lista dos SSIDs que existiam ali.

### Limite de taxa e parâmetros recusados

- HTTP 429: espera 60 segundos e repete a chamada uma vez.
- HTTP 400 com o parâmetro de visão sem fio: repete a chamada sem esse parâmetro.

### Quando a lista individual não existe

HTTP 404 em `dna/data/api/v1/clients` significa que esta versão do Catalyst Center não oferece a lista de clientes.

Na consulta de **todos os clientes**, o script passa para a saúde agregada por localidade: `GET /dna/intent/api/v1/site-health`, paginada de 50 em 50. Cada linha é um site, com percentuais e contagens de clientes cabeados e sem fio. Não há MAC, SSID nem reincidência. Se havia filtro de SSID, a consulta para com erro, porque esse fallback não traz a rede.

Na consulta de **reincidentes**, o 404 encerra com erro. Sem a lista individual não dá para saber se o mesmo cliente repetiu a conexão baixa.

## 5. Normalização de cada cliente

Cada registro da API vira uma linha plana. Campos aninhados (`health`, `connection`, `traffic`, `connectedNetworkDevice`) são lidos junto com os campos já planos. Ficam de fora os valores vazios.

O que entra na linha, quando a API envia:

- Identidade: MAC, nome, usuário, ID do usuário, tipo, status da conexão (traduzido: Conectado, Desconectado, Conectando, Desconhecido).
- Scores: saúde geral, onboarding e conectividade. O score pode vir solto ou na lista `healthScore`. Valor negativo é descartado.
- Rede: IPv4, IPv6, localidade, SSID, banda, RSSI, SNR, VLAN, canal, protocolo, autenticação, WLC.
- Equipamento: sistema, versão, fabricante, tipo, formato, dispositivo ao qual o cliente está ligado, IP e interface desse dispositivo.
- Tráfego: uso, bytes enviados e recebidos.
- Tempo: duração da sessão (a API manda milissegundos; o relatório mostra horas e minutos) e horário da última atualização.

Colunas do catálogo que ficaram vazias em todas as linhas não aparecem no CSV nem na tabela. Se não houver nenhuma linha, o relatório usa o catálogo completo.

## 6. Modo reincidentes

Este modo responde a uma pergunta diferente: o mesmo cliente apareceu com conectividade baixa em intervalos distintos do período?

### Divisão do período

O período é cortado em fatias. O tamanho depende da duração:

| Duração do período | Tamanho de cada fatia |
| --- | --- |
| Até 1 hora | 12 minutos |
| Até 3 horas | 30 minutos |
| Até 6 horas | 1 hora |
| Até 12 horas | 2 horas |
| Até 24 horas | 4 horas |
| Até 3 dias | 12 horas |
| Mais que 3 dias | 1 dia |

O número de fatias não passa de 31. Se a conta ultrapassar isso, as fatias são redistribuídas para caber no limite.

Cada fatia gera uma consulta própria de clientes, com o início e o fim daquela janela. O terminal mostra o intervalo atual e quantos clientes com conexão baixa apareceram nele.

### O que conta como ocorrência

Dentro de cada fatia:

- O cliente precisa ter MAC.
- O mesmo MAC conta uma vez naquela fatia.
- O score de conectividade precisa estar na faixa escolhida. O padrão é de 1 a 7. Score 0 é cliente inativo e fica de fora. Scores 8, 9 e 10 também ficam de fora do padrão.
- A faixa da página aceita qualquer par de 1 a 10; o menor e o maior são ordenados antes da comparação.

As ocorrências de todas as fatias são agrupadas pelo MAC. O cliente entra no relatório quando o número de fatias em que ele apareceu é pelo menos o mínimo informado (padrão 3).

A linha agregada traz:

- Quantidade de ocorrências e a lista dos intervalos.
- Pior conectividade, pior saúde geral, pior RSSI e pior SNR entre as ocorrências.
- Nome, usuário, tipo, SSID e dispositivo conectado da ocorrência mais recente.
- Localidade mais frequente. As demais localidades vão em “Outras localidades”.
- Horário de início da primeira ocorrência e horário de fim da última.

A ordenação é: mais ocorrências primeiro, depois a pior conectividade, a localidade e o MAC. O terminal também imprime a contagem de reincidentes por localidade.

## 7. Gravação dos relatórios

Os arquivos vão para a pasta `relatorios/`, criada se ainda não existir. O nome leva a data e a hora da geração:

- Todos os clientes, ou o fallback por localidade: `clientes-saude-AAAAMMDD-HHMMSS.csv` e `.html`
- Reincidentes: `clientes-reincidentes-AAAAMMDD-HHMMSS.csv` e `.html`

O CSV usa UTF-8 com marca de ordem de bytes, para abrir com acentos no Excel. A primeira linha são os rótulos das colunas.

O HTML é um dashboard estático. O subtítulo registra localidade, tipo, SSIDs, faixa de conexão e mínimo (no modo reincidentes), período, notas da consulta, host e o caminho do CSV. A senha não aparece.

No terminal, o navegador abre esse HTML. Na página local, o mesmo HTML volta na resposta, com uma barra no topo: nova consulta, download do CSV e quantidade de registros. O download só entrega arquivos cujo nome segue o padrão `clientes-saude-…csv` ou `clientes-reincidentes-…csv` dentro de `relatorios/`.

A página atende uma consulta por vez. Uma segunda tentativa enquanto a primeira ainda roda recebe o aviso de que já existe uma consulta em andamento.

## 8. Leitura do dashboard

O HTML não consulta o Catalyst Center de novo. Filtros, ordenação e movimento de colunas acontecem no navegador.

- **Busca geral** procura o texto em todas as colunas visíveis.
- **Saúde** classifica o primeiro score disponível na linha. Na escala de 0 a 10: 0 é inativo, 1 a 3 é ruim, 4 a 7 é regular, 8 a 10 é boa. Em percentual: abaixo de 40 é ruim, de 40 a 79 é regular, 80 ou mais é boa.
- **Filtro por coluna** e **ordenação** por clique no título.
- **Arrastar** o controle da coluna muda a ordem horizontal. “Restaurar colunas” volta à ordem original.
- **Cartões** mostram linhas visíveis, quantidade de cabeados e sem fio quando o tipo existe, total de clientes no modo por localidade, e a média do score usado na classificação de saúde.

## Códigos de saída do terminal

| Código | Significado |
| --- | --- |
| 0 | Consulta concluída, ou cancelada quando o usuário recusa consultar todas as localidades. |
| 1 | Credenciais recusadas sem nova tentativa, ou erro do Catalyst Center. |
| 2 | A entrada terminou antes de os dados serem informados. |
| 130 | Consulta interrompida com Ctrl+C. |

## Mapa dos arquivos

| Arquivo | Papel nesta consulta |
| --- | --- |
| `consultar.py` | Atalho para o fluxo interativo. |
| `pagina.py` | Atalho para a página local. |
| `Consulta.html` | Formulário servido pela página. |
| `catalyst_center/consultar.py` | Perguntas, orquestração, filtros, CSV e abertura do dashboard. |
| `catalyst_center/web.py` | Servidor local, validação do formulário e entrega do resultado. |
| `catalyst_center/client.py` | Token, paginação e chamadas da API. |
| `catalyst_center/sites.py` | Casa o texto da localidade com a hierarquia de sites. |
| `catalyst_center/health.py` | Linha plana do cliente, filtro de localidade e de SSID, colunas. |
| `catalyst_center/recurrence.py` | Fatias do período e agregação dos reincidentes. |
| `catalyst_center/dashboard.py` | HTML do relatório. |
| `relatorios/` | CSV e HTML gerados. |
