# Integra-O — Sistema de Captura de Peso de Balanças

Sistema completo para **ler o peso de balanças** via **RS232 (porta serial)** ou
**TCP/IP (rede)**, exibir o peso em tempo real em um **painel no navegador**,
aplicar **tara** e **zeragem**, e **salvar automaticamente cada pesagem na
nuvem** (OneDrive, Google Drive, WebDAV ou pasta local).

O sistema roda na **máquina local** e é acessado por qualquer navegador — na
própria máquina (`localhost`) ou de qualquer computador da mesma rede.

---

## ✨ Principais recursos

| Recurso | Descrição |
|---|---|
| 🔌 **RS232 / Serial** | Suporte a portas COM (Windows) e `/dev/tty*` (Linux), com baudrate, paridade, data bits e stop bits configuráveis. |
| 🌐 **TCP/IP** | Conexão como cliente a balanças com servidor TCP embutido (host/porta configuráveis). |
| 🧩 **Protocolos** | Genérico, Mettler Toledo, **Mettler Toledo TI400 (P03/P10/P08/auto)**, Filizola, CAS, A&D e **regex personalizado**. |
| 📺 **Painel em tempo real** | Peso exibido ao vivo via WebSocket (sem recarregar a página). |
| ⚖️ **Tara** | Capturar tara do peso atual, definir tara manual ou limpar a tara. |
| 0️⃣ **Zerar balança** | Zera o offset de leitura pelo sistema. |
| 💾 **Salvamento automático** | Cada pesagem estabilizada é salva automaticamente. |
| ☁️ **Nuvem** | OneDrive, Google Drive (pasta sincronizada **ou** API), WebDAV e pasta local. |
| 📋 **Histórico** | Tabela de pesagens com exportação para CSV. |
| 🔁 **Reconexão automática** | Reconecta sozinho se a balança cair. |
| 🖥️ **Multi-máquina** | Acessível de qualquer PC da rede pelo IP do servidor. |

---

## 📁 Estrutura do projeto

```
integra-o/
├── backend/
│   ├── app.py                  # Servidor Flask + SocketIO (API REST + WebSocket)
│   ├── data/                   # Pasta padrão de salvamento local
│   └── scale/
│       ├── config.py           # Gerenciamento de configuração
│       ├── connections.py      # Conexões RS232 (pyserial) e TCP/IP (socket)
│       ├── protocols.py        # Parsers de protocolos de balança
│       ├── cloud.py            # Sincronização em nuvem (múltiplos provedores)
│       └── manager.py          # Núcleo: leitura, tara, zeragem, estabilidade
├── frontend/
│   ├── index.html              # Painel de pesagem
│   ├── css/style.css           # Estilo (tema escuro profissional)
│   └── js/app.js               # Lógica do painel (WebSocket, comandos, config)
├── config/
│   └── config.json             # Configuração persistida
├── scripts/
│   ├── simulador_balanca.py    # Simulador de balança TCP (inclui TI400 P03)
│   ├── testar_sistema.py       # Testes dos módulos internos
│   ├── testar_ti400.py         # Testes dos protocolos Mettler Toledo TI400
│   ├── testar_serial_pty.py    # Teste de leitura serial (pseudo-terminal)
│   └── gerar_zip.sh            # Gera o ZIP de distribuição
├── docs/
│   └── MANUAL.md               # Manual de uso detalhado
├── run.py                      # Ponto de entrada
├── requirements.txt            # Dependências Python
├── iniciar_windows.bat         # Inicialização no Windows
├── iniciar_linux.sh            # Inicialização no Linux/macOS
└── README.md
```

---

## 🚀 Instalação e uso

### Pré-requisitos
- **Python 3.9+** (marque "Add Python to PATH" no Windows).
- Navegador moderno (Chrome, Edge, Firefox).

### Windows
1. Baixe e extraia o ZIP.
2. Dê duplo clique em **`iniciar_windows.bat`**.
3. Na primeira execução, ele cria o ambiente virtual e instala as dependências.
4. Abra o navegador em **http://localhost:5000**.

### Linux / macOS
```bash
chmod +x iniciar_linux.sh
./iniciar_linux.sh
```
Depois abra **http://localhost:5000**.

### Manual (qualquer sistema)
```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

---

## ⚙️ Configuração

Toda a configuração é feita **pela própria interface** (painel lateral →
*Configurações*) e fica salva em `config/config.json`.

### 1. Conexão
- **RS232 (Serial):** selecione a porta (`COM1`, `/dev/ttyUSB0`...), baudrate,
  data bits, paridade e stop bits. Use o botão 🔍 para detectar portas.
- **TCP/IP:** informe o **IP** e a **porta** da balança (ex.: `192.168.0.100:4001`).

### 2. Protocolo
Escolha o protocolo da sua balança. Se ela envia o número "cru" (ex.: `12345`
para 12,345 kg), use o protocolo **Genérico** com **3 casas decimais
implícitas**. Para formatos especiais, use **Personalizado (Regex)**.

**Indicador Mettler Toledo TI400:** conectado pela **rede (TCP/IP)**, o TI400
envia o protocolo **P03**, que é um **quadro binário de tamanho fixo** — não é
texto. Por isso o protocolo **Mettler Toledo / Toledo** (`ST,GS,+…`) **não
funciona** com o TI400 na rede. Selecione:

- **Mettler Toledo TI400 — Automático** (recomendado): detecta P03 (binário) ou
  ASCII (P10/P08) automaticamente.
- **Mettler Toledo TI400 — P03**: quadro binário de 18 bytes, usado no
  socket Ethernet/WiFi (e também no serial).
- **Mettler Toledo TI400 — P10** / **P08**: variantes de texto.

Para descobrir o protocolo certo, veja o campo **Monitor da balança — bytes
recebidos** na aba *Protocolo* (mostra os bytes crus em hexadecimal + ASCII).

### 3. Pesagem
Defina unidade (kg/g/t/lb), tara padrão, limiar e tempo de estabilidade, peso
mínimo válido e o intervalo entre salvamentos automáticos.

### 4. Nuvem
Escolha o destino:

| Provedor | Como funciona |
|---|---|
| **Pasta Local** | Grava em uma pasta do computador. |
| **OneDrive (pasta sincronizada)** | Aponte para a pasta local que o app do OneDrive sincroniza (ex.: `C:\Users\Você\OneDrive\Pesagens`). O arquivo vai para a nuvem automaticamente. |
| **Google Drive (pasta sincronizada)** | Igual ao anterior, usando a pasta do Google Drive para Desktop. |
| **OneDrive API** | Envio direto via Microsoft Graph (requer `client_id`, `client_secret` e `refresh_token`). |
| **Google Drive API** | Envio direto via Google Drive API (requer arquivo de credenciais de *service account* ou token OAuth). |
| **WebDAV** | Nextcloud, ownCloud e outros servidores WebDAV. |

Formatos de arquivo: **CSV**, **JSON** ou **TXT**. Use o botão **Testar
Destino** para validar a gravação.

> 💡 **Dica:** o método mais simples e confiável para OneDrive/Google Drive é
> usar a **pasta sincronizada** — não exige configuração de API/OAuth.

---

## 🧪 Testando sem uma balança real

O projeto inclui um **simulador de balança TCP**:

```bash
python scripts/simulador_balanca.py --port 4001 --protocol toledo
```

Depois, no painel, configure a conexão como **TCP/IP** apontando para
`127.0.0.1:4001` e o protocolo **Mettler Toledo**. O peso aparecerá mudando
automaticamente.

Para simular um **indicador Mettler Toledo TI400** (quadro binário P03, igual ao
do socket de rede real):

```bash
python scripts/simulador_balanca.py --port 4001 --protocol ti400_p03
```

E no painel escolha o protocolo **Mettler Toledo TI400 — P03** (ou **Automático**).

Teste dos módulos internos:
```bash
python scripts/testar_sistema.py
python scripts/testar_ti400.py        # protocolos TI400 (P03/P10/P08/auto)
python scripts/testar_serial_pty.py   # leitura serial via pseudo-terminal
```

---

## 🌐 Acesso pela rede

Por padrão o servidor escuta em `0.0.0.0:5000`. Para acessar de outra máquina:

1. Descubra o IP do computador que roda o sistema (`ipconfig` no Windows ou
   `ip a` no Linux).
2. No outro computador, abra `http://IP_DO_SERVIDOR:5000`.
3. Libere a porta 5000 no firewall, se necessário.

Para uso **somente local**, altere o *Host do servidor* para `127.0.0.1` na
aba **Sistema**.

---

## 🔒 Segurança e observações

- O token do GitHub, credenciais de API e senhas do WebDAV **não** são
  exibidos em logs.
- A gravação em nuvem é feita por uma **fila assíncrona**: a leitura da balança
  nunca é bloqueada por lentidão de rede.
- Em caso de falha de envio, a pesagem é **reenfileirada** para nova tentativa.

---

## 🛠️ Solução de problemas

| Sintoma | Causa provável / Solução |
|---|---|
| "Falha ao abrir porta serial" | Porta errada ou em uso. Confira com o botão 🔍 e feche outros programas. |
| Peso não aparece | Protocolo/baudrate incorretos. Teste com o simulador e ajuste o protocolo. No **TI400 via rede**, selecione **Mettler Toledo TI400 — P03** ou **Automático** (o formato `ST,GS,+…` não funciona nesse indicador). |
| Peso "congelado" | Cabo/rede caíram. O sistema reconecta sozinho; verifique o log. |
| Não salva na nuvem | Verifique a pasta de destino e o botão *Testar Destino*. |
| Porta 5000 em uso | Altere a porta na aba **Sistema** e reinicie o servidor. |

---

## 📄 Licença

Projeto fornecido como está, para uso livre na integração de balanças.
