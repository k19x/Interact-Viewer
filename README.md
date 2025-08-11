# 📡 Interactsh Viewer

O **Interactsh Viewer** é uma aplicação web moderna e responsiva para monitoramento de interações **HTTP** e **DNS** capturadas pelo [`interactsh-client`](https://github.com/projectdiscovery/interactsh).  
Ideal para **pentests**, **provas de conceito (PoCs)** e **demonstração de vulnerabilidades** que requerem callbacks controlados.

---

## 🖼️ Demonstração da Interface

<img width="1917" height="1032" alt="image" src="https://github.com/user-attachments/assets/ea542a9f-84ae-4926-88bc-510f5ffce268" />

---

## 🚀 Funcionalidades

- **Visualização em tempo real** via **SSE (Server-Sent Events)**.
- Exibição do **payload atual** para uso em testes.
- **Filtro por tipo de interação**: HTTP, DNS ou todos.
- Layout **dark mode** otimizado para longos períodos de uso.
- Interface responsiva (desktop e mobile).
- Botões para **Iniciar, Reiniciar e Parar** o cliente `interactsh`.
- Ajuste automático de colunas para **evitar quebras de linha ou barras de rolagem**.

---

## 📦 Requisitos

Antes de usar, é necessário ter instalado:

- [Docker](https://docs.docker.com/get-docker/) ou o binário do [`interactsh-client`](https://github.com/projectdiscovery/interactsh/releases)
- Navegador moderno (Chrome, Firefox, Edge, Brave, etc.)

---

## ⚙️ Configuração

1. **Clonar este repositório**:

```bash
git clone https://github.com/seu-usuario/interactsh-viewer.git
cd interactsh-viewer
```
2. **Executar o interactsh-client via Docker:
```bash
docker run -it --rm \
  -p 8080:8080 \
  projectdiscovery/interactsh-client:latest \
  --server https://oast.pro --dns-port 53 --http-port 80
```
```bash
├── index.html       # Interface principal
├── main.js          # Lógica de interação e SSE
├── style.css        # Estilização (CSS)
├── docs/
│   └── screenshot.png
└── README.md
```
