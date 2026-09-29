import json
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGridLayout, QScrollArea, QFrame, QMessageBox, QStackedWidget,
    QLineEdit, QInputDialog, QSizePolicy
)
from PySide6.QtGui import QPixmap, QDesktopServices
from PySide6.QtCore import Qt, QUrl, QTimer

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


BASE_DIR = Path(__file__).resolve().parent
IMAGENS = BASE_DIR / "imagens"
PDF_DIR = BASE_DIR / "comandas"

MAX_COLUNAS = 4

# Senha de acesso ao painel do administrador
ADMIN_SENHA = "dayse_estelionato"


# ==========================================================
# Helpers
# ==========================================================

def formatar_moeda(valor):
    if valor is None:
        return "R$ --"
    return f"R$ {valor:.2f}".replace(".", ",")


def slug(texto):
    """'Sonho de Valsa' -> 'sonho_de_valsa' (para nome de arquivo)."""
    texto = texto.lower()
    substituicoes = {
        "á": "a", "à": "a", "ã": "a", "â": "a",
        "é": "e", "ê": "e",
        "í": "i",
        "ó": "o", "õ": "o", "ô": "o",
        "ú": "u",
        "ç": "c",
    }
    for origem, destino in substituicoes.items():
        texto = texto.replace(origem, destino)
    texto = texto.replace("(", "").replace(")", "")
    return "_".join(texto.split())


def imagem_existe(nome):
    if not nome:
        return False
    return (IMAGENS / Path(nome).name).exists()



# ==========================================================
# Persistência das comandas pendentes (painel do admin)
# ==========================================================

ARQUIVO_COMANDAS = BASE_DIR / "comandas_pendentes.json"


def carregar_comandas_pendentes():
    """Lê o arquivo e devolve a lista já ordenada por chegada."""
    if not ARQUIVO_COMANDAS.exists():
        return []

    try:
        dados = json.loads(ARQUIVO_COMANDAS.read_text(encoding="utf-8"))
        if not isinstance(dados, list):
            return []
        dados.sort(key=lambda c: c.get("timestamp", 0))
        return dados
    except Exception:
        return []


def salvar_lista_comandas(comandas):
    ARQUIVO_COMANDAS.write_text(
        json.dumps(comandas, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def salvar_comanda_pendente(comanda):
    comandas = carregar_comandas_pendentes()
    comandas.append(comanda)
    salvar_lista_comandas(comandas)


def remover_comanda_pendente(comanda_id):
    comandas = carregar_comandas_pendentes()
    comandas = [c for c in comandas if c.get("id") != comanda_id]
    salvar_lista_comandas(comandas)


def comanda_tem_imediato(comanda):
    return any(item.get("imediato") for item in comanda.get("itens", []))


# ==========================================================
# Card do produto (totem)
# ==========================================================

class CardProduto(QFrame):
    """Card que se expande ao clique.

    Se o produto tiver `sabores`, ao clicar mostra a descrição + uma coluna
    com cada sabor (miniatura, nome e preço). Clicar em um sabor adiciona ao
    carrinho como "<produto> - <sabor>".
    """

    def __init__(self, produto, adicionar_callback):
        super().__init__()

        self.produto = produto
        self.adicionar_callback = adicionar_callback
        self.aberto = False
        self.sabores = produto.get("sabores") or []
        self.tem_sabores = len(self.sabores) > 0

        self.setObjectName("card")
        self.setMinimumHeight(385)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)

        # ---------- Imagem principal ----------
        self.imagem = QLabel()
        self.imagem.setMinimumHeight(245)
        self.imagem.setMaximumHeight(245)
        self.imagem.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.imagem.setAlignment(Qt.AlignCenter)
        self.imagem.setStyleSheet("""
            QLabel {
                background: #f0f0f0;
                border-radius: 12px;
                color: #999999;
                font-size: 15px;
            }
        """)
        self.carregar_imagem()
        layout.addWidget(self.imagem)

        # ---------- Nome ----------
        self.nome = QLabel(produto["nome"])
        self.nome.setAlignment(Qt.AlignCenter)
        self.nome.setWordWrap(True)
        self.nome.setFixedHeight(45)
        self.nome.setStyleSheet("""
            QLabel {
                color: #202020;
                font-size: 17px;
                font-weight: bold;
                background: transparent;
            }
        """)
        layout.addWidget(self.nome)

        # ---------- Linha de preço + botão "+" ----------
        self.detalhes = QFrame()
        detalhes_layout = QHBoxLayout(self.detalhes)
        detalhes_layout.setContentsMargins(4, 0, 4, 0)

        self.preco = QLabel(self.formatar_preco())
        self.preco.setStyleSheet("""
            QLabel {
                color: #202020;
                font-size: 17px;
                font-weight: bold;
                background: transparent;
            }
        """)

        self.botao_adicionar = QPushButton("+")
        self.botao_adicionar.setFixedSize(44, 44)
        self.botao_adicionar.clicked.connect(self.adicionar)
        self.botao_adicionar.setStyleSheet("""
            QPushButton {
                background: #1769aa;
                color: white;
                border: none;
                border-radius: 22px;
                font-size: 25px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #0d4f82;
            }
        """)

        detalhes_layout.addWidget(self.preco)
        detalhes_layout.addStretch()
        detalhes_layout.addWidget(self.botao_adicionar)

        # ---------- Descrição ----------
        self.descricao = QLabel(produto["descricao"])
        self.descricao.setWordWrap(True)
        self.descricao.setAlignment(Qt.AlignCenter)
        self.descricao.setStyleSheet("""
            QLabel {
                color: #777777;
                font-size: 12px;
                background: transparent;
            }
        """)

        # ---------- Painel de sabores ----------
        self.painel_sabores = QWidget()
        self.painel_sabores.setVisible(False)
        sabores_layout = QVBoxLayout(self.painel_sabores)
        sabores_layout.setContentsMargins(0, 4, 0, 0)
        sabores_layout.setSpacing(6)

        titulo_sabores = QLabel("Escolha o sabor:")
        titulo_sabores.setStyleSheet("""
            QLabel {
                color: #245b8f;
                font-size: 12px;
                font-weight: bold;
                background: transparent;
            }
        """)
        sabores_layout.addWidget(titulo_sabores)

        for sabor in self.sabores:
            sabores_layout.addWidget(self.criar_linha_sabor(sabor))

        self.detalhes.setVisible(False)
        self.descricao.setVisible(False)

        layout.addWidget(self.detalhes)
        layout.addWidget(self.descricao)
        layout.addWidget(self.painel_sabores)

        self.setStyleSheet("""
            QFrame#card {
                background: white;
                border: 1px solid #c5dcf5;
                border-radius: 14px;
            }
            QFrame#card:hover {
                border: 2px solid #4d8edb;
            }
        """)

    # ------------------------------------------------------

    def criar_linha_sabor(self, sabor):
        linha = QFrame()
        linha.setObjectName("sabor_linha")
        linha.setCursor(Qt.PointingHandCursor)
        linha.setStyleSheet("""
            QFrame#sabor_linha {
                background: #f5faff;
                border: 1px solid #d0e3f7;
                border-radius: 8px;
            }
            QFrame#sabor_linha:hover {
                background: #e6f1ff;
                border: 1px solid #4d8edb;
            }
        """)

        h = QHBoxLayout(linha)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(8)

        miniatura = QLabel()
        miniatura.setFixedSize(52, 52)
        miniatura.setAlignment(Qt.AlignCenter)
        miniatura.setStyleSheet("""
            QLabel {
                background: #ffffff;
                border: 1px solid #e0eefb;
                border-radius: 6px;
                color: #b0b0b0;
                font-size: 18px;
            }
        """)
        miniatura.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        pixmap = self.pixmap_do_sabor(sabor)
        if not pixmap.isNull():
            miniatura.setPixmap(
                pixmap.scaled(
                    52, 52,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
            )
        else:
            miniatura.setText("📷")

        h.addWidget(miniatura)

        nome = QLabel(sabor["nome"])
        nome.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        nome.setStyleSheet("""
            QLabel {
                color: #202020;
                font-size: 13px;
                font-weight: bold;
                background: transparent;
            }
        """)
        h.addWidget(nome)
        h.addStretch()

        preco_valor = sabor.get("preco", self.produto.get("preco"))
        preco = QLabel(formatar_moeda(preco_valor))
        preco.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        preco.setStyleSheet("""
            QLabel {
                color: #1769aa;
                font-size: 13px;
                font-weight: bold;
                background: transparent;
            }
        """)
        h.addWidget(preco)

        def _click(event, s=sabor):
            if event.button() == Qt.LeftButton:
                self.adicionar_sabor(s)

        linha.mousePressEvent = _click
        return linha

    def pixmap_do_sabor(self, sabor):
        """Tenta carregar a imagem do sabor.

        1) Se o sabor tem 'imagem' explícita, tenta essa.
        2) Senão, tenta '<base_produto>_<slug_sabor>.jpg'.
        3) Senão, tenta '<base_produto>_<slug_sabor>.png'.
        4) Senão, devolve QPixmap() vazio.
        """
        candidatos = []

        if sabor.get("imagem"):
            candidatos.append(IMAGENS / Path(sabor["imagem"]).name)

        base = slug(Path(self.produto["imagem"]).stem)
        candidatos.append(IMAGENS / f"{base}_{slug(sabor['nome'])}.jpg")
        candidatos.append(IMAGENS / f"{base}_{slug(sabor['nome'])}.png")

        for caminho in candidatos:
            pixmap = QPixmap(str(caminho))
            if not pixmap.isNull():
                return pixmap

        return QPixmap()

    # ------------------------------------------------------

    def caminho_imagem(self):
        return IMAGENS / Path(self.produto["imagem"]).name

    def carregar_imagem(self):
        caminho = self.caminho_imagem()
        pixmap = QPixmap(str(caminho))

        if pixmap.isNull():
            self.imagem.setText("Imagem não encontrada")
            return

        self._pixmap_original = pixmap
        self.atualizar_imagem()

    def atualizar_imagem(self):
        pixmap = getattr(self, "_pixmap_original", QPixmap())
        if pixmap.isNull():
            return

        largura = max(120, self.imagem.width() - 4)
        altura = max(180, self.imagem.height() - 4)

        self.imagem.setPixmap(
            pixmap.scaled(
                largura,
                altura,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
        )

    def resizeEvent(self, event):
        self.atualizar_imagem()
        super().resizeEvent(event)

    def formatar_preco(self):
        return formatar_moeda(self.produto.get("preco"))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.botao_adicionar.isVisible() and \
                    self.botao_adicionar.geometry().contains(event.pos()):
                super().mousePressEvent(event)
                return
            self.abrir_detalhes()
        super().mousePressEvent(event)

    def abrir_detalhes(self):
        self.aberto = not self.aberto

        if self.tem_sabores:
            self.detalhes.setVisible(False)
            self.descricao.setVisible(self.aberto)
            self.painel_sabores.setVisible(self.aberto)
        else:
            self.detalhes.setVisible(self.aberto)
            self.descricao.setVisible(self.aberto)
            self.painel_sabores.setVisible(False)

    def adicionar(self):
        if self.tem_sabores:
            self.aberto = True
            self.descricao.setVisible(True)
            self.painel_sabores.setVisible(True)
            return

        if self.produto.get("preco") is None:
            QMessageBox.information(
                self,
                "Produto",
                "Este produto ainda não possui preço cadastrado."
            )
            return

        self.adicionar_callback(self.produto)

    def adicionar_sabor(self, sabor):
        preco = sabor.get("preco", self.produto.get("preco"))

        if preco is None:
            QMessageBox.information(
                self,
                "Produto",
                "Este sabor ainda não possui preço cadastrado."
            )
            return

        produto_com_sabor = dict(self.produto)
        produto_com_sabor["nome"] = f"{self.produto['nome']} - {sabor['nome']}"
        produto_com_sabor["preco"] = preco
        produto_com_sabor["sabor"] = sabor["nome"]
        produto_com_sabor.pop("sabores", None)

        # Escolhe a imagem do item: prioriza a do sabor se existir;
        # senão, tenta o nome derivado; senão, mantém a do produto.
        base_stem = Path(self.produto["imagem"]).stem
        base_slug = slug(base_stem)
        candidato_sabor = f"{base_slug}_{slug(sabor['nome'])}.jpg"

        if sabor.get("imagem") and imagem_existe(sabor["imagem"]):
            produto_com_sabor["imagem"] = sabor["imagem"]
        elif imagem_existe(candidato_sabor):
            produto_com_sabor["imagem"] = candidato_sabor
        # senão mantém self.produto["imagem"]

        self.adicionar_callback(produto_com_sabor)


# ==========================================================
# Card de comanda (painel do admin)
# ==========================================================

class CardComanda(QFrame):
    """Card que representa uma comanda no painel do administrador."""

    def __init__(self, comanda, marcar_pronto_callback):
        super().__init__()

        self.comanda = comanda
        self.marcar_pronto_callback = marcar_pronto_callback

        self.setFixedWidth(290)
        self.setMinimumHeight(230)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)

        tem_im = comanda_tem_imediato(comanda)
        cor_borda = "#f59e0b" if tem_im else "#c5dcf5"

        self.setStyleSheet(f"""
            QFrame {{
                background: white;
                border: 2px solid {cor_borda};
                border-radius: 12px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # Cabeçalho: número + horário
        topo = QHBoxLayout()

        numero = QLabel(f"#{comanda['numero']:04d}")
        numero.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #1769aa; "
            "background: transparent; border: none;"
        )
        topo.addWidget(numero)
        topo.addStretch()

        try:
            hora_txt = datetime.fromisoformat(
                comanda["criada_em"]
            ).strftime("%H:%M:%S")
        except Exception:
            hora_txt = "--:--"

        hora = QLabel(hora_txt)
        hora.setStyleSheet(
            "font-size: 12px; color: #888; "
            "background: transparent; border: none;"
        )
        topo.addWidget(hora)
        layout.addLayout(topo)

        # Nome
        nome = QLabel(comanda["nome"])
        nome.setWordWrap(True)
        nome.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: #222; "
            "background: transparent; border: none;"
        )
        layout.addWidget(nome)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(
            "background: #e0e0e0; max-height: 1px; border: none;"
        )
        layout.addWidget(sep)

        # Itens
        for item in comanda["itens"]:
            linha = QLabel(f"{item['quantidade']}×  {item['nome']}")
            linha.setWordWrap(True)

            if item.get("imediato"):
                linha.setStyleSheet(
                    "font-size: 13px; color: #b45309; font-weight: bold; "
                    "background: transparent; border: none;"
                )
            else:
                linha.setStyleSheet(
                    "font-size: 13px; color: #333; "
                    "background: transparent; border: none;"
                )

            layout.addWidget(linha)

        layout.addStretch()

        if "total" in comanda:
            total_lbl = QLabel(f"Total: {formatar_moeda(comanda['total'])}")
            total_lbl.setStyleSheet(
                "font-size: 12px; color: #666; "
                "background: transparent; border: none;"
            )
            layout.addWidget(total_lbl)

        pronto = QPushButton("✓ Pronto")
        pronto.setFixedHeight(34)
        pronto.setCursor(Qt.PointingHandCursor)
        pronto.clicked.connect(
            lambda: self.marcar_pronto_callback(self.comanda["id"])
        )
        pronto.setStyleSheet("""
            QPushButton {
                background: #16a34a;
                color: white;
                border: none;
                border-radius: 7px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover { background: #15803d; }
        """)
        layout.addWidget(pronto)


# ==========================================================
# Página do carrinho (totem)
# ==========================================================

class PaginaCarrinho(QWidget):
    """Página separada para visualizar, aumentar, diminuir e excluir produtos."""

    def __init__(self, voltar_callback, finalizar_callback,
                 excluir_callback=None):
        super().__init__()

        self.voltar_callback = voltar_callback
        self.finalizar_callback = finalizar_callback
        self.excluir_callback = excluir_callback
        self.itens = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(35, 25, 35, 25)
        layout.setSpacing(15)

        topo = QHBoxLayout()

        voltar = QPushButton("← Voltar")
        voltar.setFixedSize(120, 45)
        voltar.clicked.connect(self.voltar_callback)
        voltar.setStyleSheet("""
            QPushButton {
                background: #eeeeee;
                border: none;
                border-radius: 8px;
                font-size: 14px;
            }
            QPushButton:hover { background: #dddddd; }
        """)
        topo.addWidget(voltar)

        titulo = QLabel("Seu pedido")
        titulo.setStyleSheet("""
            font-size: 30px;
            font-weight: bold;
            color: #222222;
        """)
        topo.addWidget(titulo)
        topo.addStretch()

        layout.addLayout(topo)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)

        self.conteudo = QWidget()
        self.lista_layout = QVBoxLayout(self.conteudo)
        self.lista_layout.setAlignment(Qt.AlignTop)
        self.lista_layout.setSpacing(10)

        self.scroll.setWidget(self.conteudo)
        layout.addWidget(self.scroll)

        rodape = QHBoxLayout()

        self.total_label = QLabel("Total: R$ 0,00")
        self.total_label.setStyleSheet("""
            font-size: 22px;
            font-weight: bold;
            color: #222222;
        """)

        rodape.addWidget(self.total_label)
        rodape.addStretch()

        finalizar = QPushButton("Conferir compra")
        finalizar.setFixedSize(190, 52)
        finalizar.clicked.connect(self.finalizar_callback)
        finalizar.setStyleSheet("""
            QPushButton {
                background: #1769aa;
                color: white;
                border: none;
                border-radius: 10px;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover { background: #444444; }
        """)

        rodape.addWidget(finalizar)
        layout.addLayout(rodape)

        self.atualizar()

    def definir_itens(self, itens):
        self.itens = itens
        self.atualizar()

    def atualizar(self):
        while self.lista_layout.count():
            item = self.lista_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self.itens:
            vazio = QLabel("Seu carrinho está vazio.")
            vazio.setAlignment(Qt.AlignCenter)
            vazio.setStyleSheet("""
                font-size: 20px;
                color: #777777;
                padding: 80px;
            """)
            self.lista_layout.addWidget(vazio)
            self.total_label.setText("Total: R$ 0,00")
            return

        total = 0

        for indice, item in enumerate(self.itens):
            produto = item["produto"]
            quantidade = item["quantidade"]
            subtotal = produto["preco"] * quantidade
            total += subtotal

            linha = QFrame()
            linha.setStyleSheet("""
                QFrame {
                    background: white;
                    border: 1px solid #dddddd;
                    border-radius: 10px;
                }
            """)

            h = QHBoxLayout(linha)
            h.setContentsMargins(15, 10, 15, 10)

            imagem = QLabel()
            imagem.setFixedSize(85, 75)
            imagem.setAlignment(Qt.AlignCenter)

            pixmap = QPixmap(str(IMAGENS / Path(produto["imagem"]).name))

            if not pixmap.isNull():
                imagem.setPixmap(
                    pixmap.scaled(
                        85, 75,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    )
                )
            else:
                imagem.setText("📷")

            h.addWidget(imagem)

            nomes = QVBoxLayout()

            nome = QLabel(produto["nome"])
            nome.setStyleSheet("font-size: 16px; font-weight: bold;")
            nomes.addWidget(nome)

            preco = QLabel(f"{formatar_moeda(produto['preco'])} cada")
            preco.setStyleSheet("color: #777777;")
            nomes.addWidget(preco)

            h.addLayout(nomes)
            h.addStretch()

            menos = QPushButton("−")
            menos.setFixedSize(38, 38)
            menos.clicked.connect(
                lambda _, i=indice: self.alterar_quantidade(i, -1)
            )

            quantidade_label = QLabel(str(quantidade))
            quantidade_label.setFixedWidth(30)
            quantidade_label.setAlignment(Qt.AlignCenter)
            quantidade_label.setStyleSheet(
                "font-size: 17px; font-weight: bold;"
            )

            mais = QPushButton("+")
            mais.setFixedSize(38, 38)
            mais.clicked.connect(
                lambda _, i=indice: self.alterar_quantidade(i, 1)
            )

            excluir = QPushButton("Excluir")
            excluir.setFixedHeight(38)
            excluir.clicked.connect(
                lambda _, i=indice: self.excluir(i)
            )

            subtotal_label = QLabel(formatar_moeda(subtotal))
            subtotal_label.setFixedWidth(90)
            subtotal_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            subtotal_label.setStyleSheet(
                "font-size: 16px; font-weight: bold;"
            )

            h.addWidget(menos)
            h.addWidget(quantidade_label)
            h.addWidget(mais)
            h.addWidget(excluir)
            h.addWidget(subtotal_label)

            self.lista_layout.addWidget(linha)

        self.total_label.setText(f"Total: {formatar_moeda(total)}")

    def alterar_quantidade(self, indice, delta):
        self.itens[indice]["quantidade"] += delta

        if self.itens[indice]["quantidade"] <= 0:
            self.itens.pop(indice)

        self.atualizar()

        if self.excluir_callback:
            self.excluir_callback()

    def excluir(self, indice):
        produto = self.itens[indice]["produto"]

        mensagem = QMessageBox(self)
        mensagem.setWindowTitle("Excluir item")
        mensagem.setIcon(QMessageBox.Warning)
        mensagem.setText(
            f"Tem certeza que deseja excluir <b>{produto['nome']}</b> "
            "do carrinho?"
        )
        mensagem.setInformativeText("Esta ação removerá o item do pedido.")
        mensagem.setStandardButtons(
            QMessageBox.Yes | QMessageBox.No
        )
        mensagem.setDefaultButton(QMessageBox.No)

        mensagem.setStyleSheet("""
            QMessageBox {
                background: white;
            }
            QMessageBox QLabel {
                color: #d00000;
                font-size: 16px;
                font-weight: bold;
            }
            QMessageBox QPushButton {
                background: #eaf3ff;
                color: #174ea6;
                border: 1px solid #b7d3f7;
                border-radius: 7px;
                padding: 7px 18px;
                min-width: 75px;
            }
            QMessageBox QPushButton:hover {
                background: #d7e9ff;
            }
        """)

        resposta = mensagem.exec()

        if resposta == QMessageBox.Yes:
            self.itens.pop(indice)
            self.atualizar()
            if self.excluir_callback:
                self.excluir_callback()

    def obter_total(self):
        return sum(
            item["produto"]["preco"] * item["quantidade"]
            for item in self.itens
        )


# ==========================================================
# Totem (cliente)
# ==========================================================

class Totem(QWidget):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Cantina do Pablo")
        self.resize(1280, 800)
        self.setMinimumSize(1000, 650)

        self.carrinho = []
        self.arquivo_contador = BASE_DIR / "contador_comandas.json"

        # ---------------------------------------------------------
        # Produtos do txt original + sabores definidos pelo cliente.
        #
        # Produtos com "sabores": ao clicar, mostram a lista de sabores.
        # Cada sabor pode ter "preco" e "imagem" próprios (opcionais).
        # Se não tiver preço, usa o preço do produto.
        # Se não tiver imagem, o sistema tenta:
        #   <produto_slug>_<sabor_slug>.jpg  (ex.: bombom_sonho_de_valsa.jpg)
        #
        # Campo "imediato": True marca o produto como prioritário no
        # painel do administrador (pratos feitos, espetos, mistos e
        # pão com ovo).
        # ---------------------------------------------------------
        self.produtos = {
            "Pratos Feitos": {
                "segunda": [
                    {
                        "nome": "Frango Empanado com Fritas",
                        "descricao": "Arroz, feijão, salada, macarrão e frango empanado com fritas",
                        "preco": 20.00,
                        "imagem": "frango_empanado.jpg",
                        "imediato": True
                    }
                ],
                "terca": [
                    {
                        "nome": "Strogonoff de Frango",
                        "descricao": "Arroz, feijão, salada, macarrão e strogonoff de frango",
                        "preco": 20.00,
                        "imagem": "strogonoff_frango.jpg",
                        "imediato": True
                    },
                    {
                        "nome": "Strogonoff de Carne",
                        "descricao": "Arroz, feijão, salada, macarrão e strogonoff de carne",
                        "preco": 20.00,
                        "imagem": "strogonoff_carne.jpg",
                        "imediato": True
                    }
                ],
                "quarta": [
                    {
                        "nome": "Churrasco - Espeto",
                        "descricao": "Arroz, feijão, salada, macarrão e churrasco no espeto",
                        "preco": 20.00,
                        "imagem": "churrasco.jpg",
                        "imediato": True
                    },
                    {
                        "nome": "Bife",
                        "descricao": "Arroz, feijão, salada, macarrão e bife",
                        "preco": 20.00,
                        "imagem": "bife.jpg",
                        "imediato": True
                    }
                ],
                "quinta": [
                    {
                        "nome": "Frango Grelhado",
                        "descricao": "Arroz, feijão, salada, macarrão e frango grelhado",
                        "preco": 20.00,
                        "imagem": "frango_grelhado.jpg",
                        "imediato": True
                    },
                    {
                        "nome": "Bife Acebolado",
                        "descricao": "Arroz, feijão, salada, macarrão e bife acebolado",
                        "preco": 20.00,
                        "imagem": "bife_acebolado.jpg",
                        "imediato": True
                    }
                ],
                "sexta": [
                    {
                        "nome": "Carne Cozida com Calabresa",
                        "descricao": "Arroz, feijão, salada, macarrão e carne cozida com calabresa",
                        "preco": 20.00,
                        "imagem": "carne_cozida.jpg",
                        "imediato": True
                    }
                ]
            },

            "Salgados": [
                {"nome": "Croissant de Presunto e Queijo", "descricao": "Croissant recheado", "preco": 7.00, "imagem": "croissant.jpg"},
                {"nome": "Risole de 4 Queijos", "descricao": "Risole recheado com quatro queijos", "preco": 5.50, "imagem": "risole.jpg"},
                {"nome": "Risole de Carne", "descricao": "Risole recheado com carne", "preco": 5.50, "imagem": "risole_carne.jpg"},
                {"nome": "Croissant 3 Queijos", "descricao": "Croissant recheado com três queijos", "preco": 6.50, "imagem": "croissant_queijo.jpg"},
                {"nome": "Hambúrguer com Cheddar", "descricao": "Hambúrguer com cheddar", "preco": 7.00, "imagem": "hamburguer.jpg"},
                {"nome": "Enroladinho de Bauru", "descricao": "Enroladinho de presunto e queijo", "preco": 7.00, "imagem": "enroladinho.jpg"},
                {"nome": "Pão de Batata com Calabresa", "descricao": "Pão de batata recheado com calabresa", "preco": 6.50, "imagem": "pao_batata_calabresa.jpg"},
                {"nome": "Pão de Batata de Frango", "descricao": "Pão de batata recheado com frango", "preco": 6.50, "imagem": "pao_batata_frango.jpg"},
                {"nome": "Esfiha de Carne", "descricao": "Esfiha recheada com carne", "preco": 7.00, "imagem": "esfiha.jpg"},
                {"nome": "Coxinha", "descricao": "Coxinha de frango", "preco": 5.50, "imagem": "coxinha.jpg"},

                {"nome": "Pão com Ovo", "descricao": "Pão com ovo e salada de alface e tomate", "preco": 5.50, "imagem": "pao_com_ovo.jpg", "imediato": True},
                {"nome": "Misto", "descricao": "Pão com presunto e queijo", "preco": 5.50, "imagem": "misto.jpg", "imediato": True},

                {"nome": "Pão de Queijo", "descricao": "Pão de queijo", "preco": 3.50, "imagem": "pao_queijo.jpg"},
                {
                    "nome": "Caldo",
                    "descricao": "Mandioca, abóbora, verde ou feijão",
                    "preco": 20.00,
                    "imagem": "caldo.jpg",
                    "sabores": [
                        {"nome": "Mandioca"},
                        {"nome": "Abóbora"},
                        {"nome": "Verde"},
                        {"nome": "Feijão"},
                    ]
                },

                {
                    "nome": "Cup Noodles",
                    "descricao": "Macarrão instantâneo",
                    "preco": 7.00,
                    "imagem": "cup.jpg",
                    "sabores": [
                        {"nome": "Cheddar"},
                        {"nome": "Galinha Caipira"},
                        {"nome": "Queijo"},
                        {"nome": "Bolonhesa"},
                    ]
                },

                {
                    "nome": "Espeto",
                    "descricao": "Carne, medalhão ou coração - quarta-feira",
                    "preco": 9.00,
                    "imagem": "espeto.jpg",
                    "imediato": True,
                    "sabores": [
                        {"nome": "Carne"},
                        {"nome": "Medalhão"},
                        {"nome": "Coração"},
                    ]
                }
            ],

            "Doces": [
                {"nome": "Paçoca", "descricao": "Paçoca", "preco": 1.00, "imagem": "pacoca.jpg"},

                {
                    "nome": "Bombom",
                    "descricao": "Bombom",
                    "preco": 2.50,
                    "imagem": "bombom.jpg",
                    "sabores": [
                        {"nome": "Sonho de Valsa"},
                        {"nome": "Ouro Branco"},
                        {"nome": "Chokito"},
                        {"nome": "Prestígio"},
                    ]
                },

                {
                    "nome": "Trento",
                    "descricao": "Chocolate Trento",
                    "preco": 4.50,
                    "imagem": "trento.jpg",
                    "sabores": [
                        {"nome": "Mousse de Maracujá"},
                        {"nome": "Torta de Limão"},
                        {"nome": "Dark"},
                        {"nome": "Avelã"},
                        {"nome": "Pistache"},
                        {"nome": "Chocolate"}
                    ]
                },

                {
                    "nome": "Biscoito",
                    "descricao": "Biscoito",
                    "preco": 3.50,
                    "imagem": "biscoito.jpg",
                    "sabores": [
                        {"nome": "Chocolate"},
                        {"nome": "Morango"},
                    ]
                },

                {"nome": "Emilia", "descricao": "Doce Emilia", "preco": 2.50, "imagem": "emilia.jpg"},

                {
                    "nome": "Halls",
                    "descricao": "Halls",
                    "preco": 2.50,
                    "imagem": "halls.jpg",
                    "sabores": [
                        {"nome": "Extra Forte (Preto)"},
                        {"nome": "Morango"},
                        {"nome": "Menta"},
                        {"nome": "Melancia"},
                    ]
                },

                {
                    "nome": "Trident",
                    "descricao": "Chiclete Trident",
                    "preco": 4.00,
                    "imagem": "trident.jpg",
                    "sabores": [
                        {"nome": "Morango"},
                        {"nome": "Tutti-fruit"},
                        {"nome": "Menta"},
                        {"nome": "Canela"},
                    ]
                },

                {"nome": "Bolo", "descricao": "Fatia de bolo", "preco": 6.00, "imagem": "bolo.jpg"},
                {"nome": "Suspiro", "descricao": "Suspiro", "preco": 3.00, "imagem": "suspiro.jpg"},
                {"nome": "Pão de Mel", "descricao": "Pão de mel", "preco": 5.00, "imagem": "pao_mel.jpg"},
                {"nome": "Copinho de Banana", "descricao": "Copinho de banana", "preco": 3.00, "imagem": "copinho_banana.jpg"},
                {"nome": "Cocada", "descricao": "Cocada", "preco": 3.00, "imagem": "cocada.jpg"},
                {"nome": "Doce de Batata Doce", "descricao": "Doce em massa de batata doce", "preco": 3.00, "imagem": "doce_batata_doce.jpg"},
                {"nome": "Cajuzinho", "descricao": "Cajuzinho", "preco": 3.00, "imagem": "cajuzinho.jpg"},

                {
                    "nome": "Geladão",
                    "descricao": "Geladão",
                    "preco": 7.00,
                    "imagem": "geladao.jpg",
                    "sabores": [
                        {"nome": "Leite Condensado"},
                        {"nome": "Morango"},
                        {"nome": "Maracujá"},
                        {"nome": "Leite em Pó"},
                        {"nome": "Paçoca"},
                    ]
                },

                {
                    "nome": "Cremosinho",
                    "descricao": "Cremosinho",
                    "preco": 2.50,
                    "imagem": "cremosinho.jpg",
                    "sabores": [
                        {"nome": "Morango"},
                        {"nome": "Maracujá"},
                        {"nome": "Leite Condensado"},
                        {"nome": "Coco"},
                        {"nome": "Uva"},
                        {"nome": "Kiwi"},
                        {"nome": "Açaí com Banana"},
                        {"nome": "Frutas Tropicais"},
                        {"nome": "Frutas Cristalizadas"},
                        {"nome": "Manga"},
                    ]
                },

                {"nome": "Maria Bolacha", "descricao": "Maria bolacha", "preco": 3.00, "imagem": "maria_bolacha.jpg"},
                {"nome": "Café", "descricao": "Café", "preco": 2.00, "imagem": "cafe.jpg"},

            ],

            "Bebidas": [
                {"nome": "Guaraná 350ml", "descricao": "Refrigerante", "preco": 5.50, "imagem": "guarana.jpg"},
                {"nome": "Fanta Uva 350ml", "descricao": "Refrigerante", "preco": 5.50, "imagem": "fanta_uva.jpg"},
                {"nome": "Fanta Laranja 350ml", "descricao": "Refrigerante", "preco": 5.50, "imagem": "fanta_laranja.jpg"},
                {"nome": "Coca-Cola Zero 350ml", "descricao": "Coca-Cola sem açúcar", "preco": 5.50, "imagem": "coca_zero.jpg"},
                {"nome": "Coca-Cola 350ml", "descricao": "Refrigerante", "preco": 5.50, "imagem": "coca.jpg"},
                {
                    "nome": "Suco Tropical 480ml",
                    "descricao": "Uva, manga, abacaxi, açaí ou goiaba",
                    "preco": 8.00,
                    "imagem": "suco_tropical.jpg",
                    "sabores": [
                        {"nome": "Uva"},
                        {"nome": "Manga"},
                        {"nome": "Abacaxi"},
                        {"nome": "Açaí"},
                        {"nome": "Goiaba"},
                    ]
                },
                {
                    "nome": "Suco Kmais 300ml",
                    "descricao": "Goiaba, laranja, uva ou maracujá",
                    "preco": 7.50,
                    "imagem": "suco_kmais.jpg",
                    "sabores": [
                        {"nome": "Goiaba"},
                        {"nome": "Laranja"},
                        {"nome": "Uva"},
                        {"nome": "Maracujá"},
                    ]
                },
                {
                    "nome": "Refrigerante Caçula 200ml",
                    "descricao": "Coca-Cola, Pepsi, Fanta Uva ou Coca-Cola Zero",
                    "preco": None,
                    "imagem": "cacula.jpg",
                    "sabores": [
                        {"nome": "Coca-Cola"},
                        {"nome": "Pepsi"},
                        {"nome": "Fanta Uva"},
                        {"nome": "Coca-Cola Zero"},
                    ]
                }
            ]
        }

        self.stack = QStackedWidget()

        self.pagina_boas_vindas = self.criar_pagina_boas_vindas()
        self.pagina_menu = self.criar_pagina_menu()

        self.pagina_carrinho = PaginaCarrinho(
            self.voltar_para_menu,
            self.confirmar_compra,
            self.atualizar_previa_carrinho,
        )

        self.stack.addWidget(self.pagina_boas_vindas)
        self.stack.addWidget(self.pagina_menu)
        self.stack.addWidget(self.pagina_carrinho)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

        self.mostrar_produtos("Pratos Feitos")
        self.atualizar_previa_carrinho()

    # ==========================================================
    # ABERTURA
    # ==========================================================

    def criar_pagina_boas_vindas(self):
        pagina = QWidget()

        layout = QVBoxLayout(pagina)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(20)

        logo = QLabel("🍽️")
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet("font-size: 75px;")
        layout.addWidget(logo)

        titulo = QLabel("Cantina do Pablo")
        titulo.setAlignment(Qt.AlignCenter)
        titulo.setStyleSheet("""
            font-size: 46px;
            font-weight: bold;
            color: #174ea6;
        """)
        layout.addWidget(titulo)

        subtitulo = QLabel("Bem vindo")
        subtitulo.setAlignment(Qt.AlignCenter)
        subtitulo.setStyleSheet("""
            font-size: 24px;
            color: #666666;
        """)
        layout.addWidget(subtitulo)

        entrar = QPushButton("Começar pedido")
        entrar.setFixedSize(250, 60)
        entrar.clicked.connect(
            lambda: self.stack.setCurrentWidget(self.pagina_menu)
        )
        entrar.setStyleSheet("""
            QPushButton {
                background: #1769aa;
                color: white;
                border: none;
                border-radius: 12px;
                font-size: 18px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #444444;
            }
        """)
        layout.addWidget(entrar, alignment=Qt.AlignCenter)

        return pagina

    # ==========================================================
    # MENU
    # ==========================================================

    def criar_pagina_menu(self):
        pagina = QWidget()

        principal = QHBoxLayout(pagina)
        principal.setContentsMargins(0, 0, 0, 0)
        principal.setSpacing(0)

        menu = QFrame()
        menu.setFixedWidth(125)
        menu.setStyleSheet("""
            QFrame {
                background: #f8fbff;
                border-right: 1px solid #c8dcf2;
            }
        """)

        menu_layout = QVBoxLayout(menu)
        menu_layout.setContentsMargins(8, 18, 8, 14)
        menu_layout.setSpacing(7)

        logo = QLabel("🍽")
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet("font-size: 34px;")
        menu_layout.addWidget(logo)

        self.criar_botao_categoria(menu_layout, "🍛\nPratos", "Pratos Feitos")
        self.criar_botao_categoria(menu_layout, "🥟\nSalgados", "Salgados")
        self.criar_botao_categoria(menu_layout, "🍰\nDoces", "Doces")
        self.criar_botao_categoria(menu_layout, "🥤\nBebidas", "Bebidas")

        menu_layout.addStretch()

        self.previa_carrinho = QLabel("Seu pedido\n0 itens\nR$ 0,00")
        self.previa_carrinho.setAlignment(Qt.AlignCenter)
        self.previa_carrinho.setStyleSheet("""
            QLabel {
                background: #ffffff;
                border: 1px solid #c5dcf5;
                border-radius: 8px;
                color: #245b8f;
                font-size: 12px;
                padding: 8px 4px;
            }
        """)
        menu_layout.addWidget(self.previa_carrinho)

        pedido = QPushButton("🛒\nCarrinho")
        pedido.setFixedHeight(65)
        pedido.clicked.connect(self.abrir_carrinho)
        pedido.setStyleSheet("""
            QPushButton {
                background: #1769aa;
                color: white;
                border: none;
                border-radius: 9px;
                font-size: 13px;
            }
            QPushButton:hover { background: #444444; }
        """)
        menu_layout.addWidget(pedido)

        principal.addWidget(menu)

        area = QFrame()
        area.setStyleSheet("QFrame { background: #edf5ff; }")
        area_layout = QVBoxLayout(area)
        area_layout.setContentsMargins(20, 16, 20, 12)
        area_layout.setSpacing(10)

        topo = QHBoxLayout()

        self.titulo = QLabel("Pratos Feitos")
        self.titulo.setStyleSheet("""
            font-size: 29px;
            font-weight: bold;
            color: #222222;
        """)
        topo.addWidget(self.titulo)
        topo.addStretch()

        self.contador = QLabel("0 itens")
        self.contador.setStyleSheet("font-size: 14px; color: #666666;")
        topo.addWidget(self.contador)

        area_layout.addLayout(topo)

        self.informacao_almoco = QLabel()
        self.informacao_almoco.setWordWrap(True)
        self.informacao_almoco.setStyleSheet("""
            QLabel {
                background: #ffffff;
                border: 1px solid #c5dcf5;
                border-radius: 9px;
                padding: 10px 13px;
                color: #245b8f;
                font-size: 13px;
            }
        """)
        area_layout.addWidget(self.informacao_almoco)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.area_produtos = QWidget()
        self.grid = QGridLayout(self.area_produtos)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(15)
        self.grid.setVerticalSpacing(15)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        scroll.setWidget(self.area_produtos)
        area_layout.addWidget(scroll)

        principal.addWidget(area, 1)

        return pagina

    def criar_botao_categoria(self, layout, texto, categoria):
        botao = QPushButton(texto)
        botao.setFixedHeight(72)
        botao.clicked.connect(lambda: self.mostrar_produtos(categoria))
        botao.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 8px;
                color: #245b8f;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #e6f1ff;
                color: #174ea6;
            }
        """)
        layout.addWidget(botao)

    # ==========================================================
    # PRODUTOS
    # ==========================================================

    def obter_dia(self):
        dias = {
            0: "segunda",
            1: "terca",
            2: "quarta",
            3: "quinta",
            4: "sexta",
            5: "sabado",
            6: "domingo"
        }
        return dias[datetime.now().weekday()]

    def limpar_grid(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def mostrar_produtos(self, categoria):
        self.titulo.setText(categoria)
        self.limpar_grid()

        for col in range(MAX_COLUNAS):
            self.grid.setColumnStretch(col, 0)
            self.grid.setColumnMinimumWidth(col, 0)

        if categoria == "Pratos Feitos":
            dia = self.obter_dia()

            nomes = {
                "segunda": "Segunda-feira",
                "terca": "Terça-feira",
                "quarta": "Quarta-feira",
                "quinta": "Quinta-feira",
                "sexta": "Sexta-feira",
                "sabado": "Sábado",
                "domingo": "Domingo"
            }

            lista = self.produtos["Pratos Feitos"].get(dia, [])

            self.informacao_almoco.setText(
                f"<b>{nomes[dia]}</b> • "
                "Todos acompanham arroz, feijão, salada e macarrão."
            )
        else:
            lista = self.produtos[categoria]
            self.informacao_almoco.setText(
                f"<b>{categoria}</b> • "
                "Clique no produto para ver preço e descrição."
            )

        if not lista:
            aviso = QLabel("Não há almoço cadastrado para hoje.")
            aviso.setAlignment(Qt.AlignCenter)
            aviso.setStyleSheet(
                "font-size: 18px; color: #777777; padding: 30px;"
            )
            self.grid.addWidget(aviso, 0, 0)
            return

        colunas = min(MAX_COLUNAS, max(1, len(lista)))

        for coluna in range(colunas):
            self.grid.setColumnStretch(coluna, 1)

        for i, produto in enumerate(lista):
            card = CardProduto(produto, self.adicionar)
            self.grid.addWidget(card, i // colunas, i % colunas)

        for coluna in range(colunas):
            self.grid.setColumnMinimumWidth(coluna, 1)

    # ==========================================================
    # CARRINHO
    # ==========================================================

    def atualizar_previa_carrinho(self):
        if not hasattr(self, "previa_carrinho"):
            return

        quantidade = sum(item["quantidade"] for item in self.carrinho)
        total = sum(
            item["produto"]["preco"] * item["quantidade"]
            for item in self.carrinho
        )

        if quantidade == 0:
            self.previa_carrinho.setText("Seu pedido\n0 itens\nR$ 0,00")
        else:
            rotulo = "item" if quantidade == 1 else "itens"
            self.previa_carrinho.setText(
                f"Seu pedido\n{quantidade} {rotulo}\n"
                f"{formatar_moeda(total)}"
            )

    def adicionar(self, produto):
        for item in self.carrinho:
            if item["produto"]["nome"] == produto["nome"]:
                item["quantidade"] += 1
                self.atualizar_contador()
                self.atualizar_previa_carrinho()
                return

        self.carrinho.append({
            "produto": produto,
            "quantidade": 1
        })
        self.atualizar_contador()
        self.atualizar_previa_carrinho()

    def atualizar_contador(self):
        quantidade = sum(item["quantidade"] for item in self.carrinho)
        rotulo = "item" if quantidade == 1 else "itens"
        self.contador.setText(f"{quantidade} {rotulo}")

    def abrir_carrinho(self):
        self.pagina_carrinho.definir_itens(self.carrinho)
        self.stack.setCurrentWidget(self.pagina_carrinho)

    def voltar_para_menu(self):
        self.atualizar_contador()
        self.atualizar_previa_carrinho()
        self.stack.setCurrentWidget(self.pagina_menu)

    # ==========================================================
    # FINALIZAÇÃO
    # ==========================================================

    def confirmar_compra(self):
        if not self.carrinho:
            QMessageBox.information(
                self,
                "Carrinho",
                "Adicione pelo menos um produto."
            )
            return

        nome, ok = self.criar_dialogo_nome()

        if not ok:
            return

        nome = nome.strip()

        if not nome:
            QMessageBox.warning(
                self,
                "Nome obrigatório",
                "Digite o nome do comprador."
            )
            return

        total = self.pagina_carrinho.obter_total()

        resposta = QMessageBox.question(
            self,
            "Confirmar compra",
            f"Comprador: {nome}\n"
            f"Total: {formatar_moeda(total)}\n\n"
            "Tem certeza que deseja confirmar a compra?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if resposta != QMessageBox.Yes:
            return

        numero_comanda = self.proximo_numero_comanda()
        caminho_pdf = self.gerar_pdf(nome, numero_comanda)

        # ---------- NOVO: grava a comanda para o painel do admin ----------
        agora = datetime.now()
        comanda = {
            "id": f"{agora.strftime('%Y%m%d')}_{numero_comanda:04d}",
            "numero": numero_comanda,
            "nome": nome,
            "criada_em": agora.isoformat(),
            "timestamp": agora.timestamp(),
            "total": total,
            "itens": [
                {
                    "nome": item["produto"]["nome"],
                    "quantidade": item["quantidade"],
                    "preco": item["produto"]["preco"],
                    "imediato": bool(item["produto"].get("imediato", False)),
                }
                for item in self.carrinho
            ],
        }
        salvar_comanda_pendente(comanda)
        # -----------------------------------------------------------------

        QMessageBox.information(
            self,
            "Compra concluída",
            "Compra confirmada!\n\n"
            f"Comanda gerada em:\n{caminho_pdf}"
        )

        self.carrinho.clear()
        self.pagina_carrinho.definir_itens(self.carrinho)
        self.atualizar_contador()
        self.atualizar_previa_carrinho()

        self.abrir_pdf(caminho_pdf)

        self.stack.setCurrentWidget(self.pagina_boas_vindas)

    def criar_dialogo_nome(self):
        nome, ok = QInputDialog.getText(
            self,
            "Identificação",
            "Digite o nome do comprador:"
        )
        return nome, ok

    # ==========================================================
    # PDF / COMANDA
    # ==========================================================

    def proximo_numero_comanda(self):
        hoje = datetime.now().strftime("%Y-%m-%d")

        try:
            if self.arquivo_contador.exists():
                dados = json.loads(
                    self.arquivo_contador.read_text(encoding="utf-8")
                )
            else:
                dados = {}
        except Exception:
            dados = {}

        if dados.get("data") != hoje:
            numero = 1
        else:
            numero = int(dados.get("numero", 0)) + 1

        dados = {
            "data": hoje,
            "numero": numero
        }

        self.arquivo_contador.write_text(
            json.dumps(dados, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        return numero

    def gerar_pdf(self, nome, numero_comanda):
        PDF_DIR.mkdir(exist_ok=True)

        agora = datetime.now()
        numero = int(numero_comanda)
        caminho = PDF_DIR / f"comanda_{agora.strftime('%Y%m%d')}_{numero:04d}.pdf"

        largura, altura = A4

        c = canvas.Canvas(str(caminho), pagesize=A4)

        margem = 17 * mm
        esquerda = margem
        direita = largura - margem

        c.setStrokeColor(colors.black)
        c.setLineWidth(1)
        c.roundRect(
            esquerda,
            25 * mm,
            direita - esquerda,
            altura - 45 * mm,
            4 * mm,
            stroke=1,
            fill=0
        )

        topo = altura - 32 * mm

        c.setFont("Helvetica-Bold", 20)
        c.drawString(esquerda + 7 * mm, topo, "Cantina do Pablo")

        c.setFont("Helvetica-Bold", 25)
        c.drawRightString(direita - 7 * mm, topo, "COMANDA")

        y = topo - 12 * mm
        c.line(esquerda, y, direita, y)

        c.setFont("Helvetica", 10)
        c.drawString(esquerda + 5 * mm, y - 8 * mm, "Nome:")

        c.setFont("Helvetica-Bold", 12)
        c.drawString(esquerda + 22 * mm, y - 8 * mm, nome[:45])

        c.setFont("Helvetica", 10)
        c.drawString(esquerda + 5 * mm, y - 18 * mm, "Data:")
        c.drawString(
            esquerda + 22 * mm,
            y - 18 * mm,
            agora.strftime("%d/%m/%Y %H:%M")
        )

        c.roundRect(direita - 48 * mm, y - 22 * mm, 42 * mm, 13 * mm, 2 * mm)

        c.setFont("Helvetica-Bold", 10)
        c.drawString(direita - 44 * mm, y - 14 * mm, "Nº")

        c.setFont("Helvetica-Bold", 13)
        c.drawRightString(direita - 10 * mm, y - 14 * mm, f"{numero:04d}")

        tabela_top = y - 28 * mm
        col1 = esquerda
        col2 = esquerda + 22 * mm
        col3 = direita - 38 * mm
        col4 = direita - 20 * mm
        tabela_bottom = 40 * mm

        c.setFont("Helvetica-Bold", 9)
        c.line(esquerda, tabela_top, direita, tabela_top)

        c.drawCentredString((col1 + col2) / 2, tabela_top - 6 * mm, "Quant.")
        c.drawCentredString((col2 + col3) / 2, tabela_top - 6 * mm, "Descrição")
        c.drawCentredString((col3 + col4) / 2, tabela_top - 6 * mm, "Vl. Unit.")
        c.drawCentredString((col4 + direita) / 2, tabela_top - 6 * mm, "Total")

        header_bottom = tabela_top - 10 * mm
        c.line(esquerda, header_bottom, direita, header_bottom)

        for x in [col2, col3, col4]:
            c.line(x, tabela_top, x, tabela_bottom)

        c.line(esquerda, tabela_top, esquerda, tabela_bottom)
        c.line(direita, tabela_top, direita, tabela_bottom)

        y_linha = header_bottom - 7 * mm
        total = 0

        for item in self.carrinho:
            produto = item["produto"]
            qtd = item["quantidade"]
            unit = produto["preco"]
            subtotal = qtd * unit
            total += subtotal

            if y_linha < tabela_bottom + 15 * mm:
                break

            c.setFont("Helvetica", 9)

            c.drawCentredString((col1 + col2) / 2, y_linha, str(qtd))

            descricao = produto["nome"][:42]
            c.drawString(col2 + 2 * mm, y_linha, descricao)

            c.drawRightString(
                col4 - 2 * mm, y_linha,
                f"{unit:.2f}".replace(".", ",")
            )
            c.drawRightString(
                direita - 2 * mm, y_linha,
                f"{subtotal:.2f}".replace(".", ",")
            )

            y_linha -= 9 * mm
            c.line(esquerda, y_linha + 3 * mm, direita, y_linha + 3 * mm)

        while y_linha > tabela_bottom + 5 * mm:
            c.line(esquerda, y_linha + 3 * mm, direita, y_linha + 3 * mm)
            y_linha -= 9 * mm

        caixa_y = 26 * mm
        c.roundRect(esquerda, caixa_y, direita - esquerda, 28 * mm, 3 * mm)

        c.setFont("Helvetica-Bold", 12)
        c.drawString(esquerda + 7 * mm, caixa_y + 16 * mm, "VALOR TOTAL R$:")

        c.roundRect(direita - 48 * mm, caixa_y + 7 * mm, 41 * mm, 14 * mm, 2 * mm)

        c.setFont("Helvetica-Bold", 14)
        c.drawRightString(
            direita - 11 * mm, caixa_y + 12 * mm,
            f"{total:.2f}".replace(".", ",")
        )

        c.setFont("Helvetica", 7)
        c.drawCentredString(largura / 2, 18 * mm, "Cantina do Pablo")

        c.save()
        return caminho

    def abrir_pdf(self, caminho):
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(caminho)))
        except Exception:
            pass


# ==========================================================
# Painel do Administrador
# ==========================================================

class JanelaAdmin(QWidget):
    """Painel do administrador: comandas pendentes divididas em
    Imediatos (topo) e Não imediatos (base), em grade, ordenadas
    por ordem de chegada."""

    COLUNAS = 4

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Painel do Administrador - Cantina do Pablo")
        self.resize(1400, 900)
        self.setMinimumSize(1000, 700)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(15)

        # ---------- Cabeçalho ----------
        topo = QHBoxLayout()

        titulo = QLabel("🍽️  Painel de Comandas")
        titulo.setStyleSheet(
            "font-size: 26px; font-weight: bold; color: #174ea6;"
        )
        topo.addWidget(titulo)
        topo.addStretch()

        self.contador = QLabel()
        self.contador.setStyleSheet("font-size: 14px; color: #666;")
        topo.addWidget(self.contador)

        atualizar_btn = QPushButton("🔄 Atualizar")
        atualizar_btn.setFixedSize(120, 38)
        atualizar_btn.clicked.connect(self.atualizar)
        atualizar_btn.setStyleSheet("""
            QPushButton {
                background: #1769aa;
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover { background: #0d4f82; }
        """)
        topo.addWidget(atualizar_btn)

        layout.addLayout(topo)

        # ---------- Área rolável ----------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        self.conteudo = QWidget()
        self.conteudo_layout = QVBoxLayout(self.conteudo)
        self.conteudo_layout.setContentsMargins(0, 0, 0, 0)
        self.conteudo_layout.setSpacing(24)

        self.secao_imediatos = self._criar_secao(
            "⚡ Imediatos",
            "Pratos feitos, espetos, mistos e pão com ovo",
            "#d97706"
        )
        self.conteudo_layout.addWidget(self.secao_imediatos["widget"])

        self.secao_nao_imediatos = self._criar_secao(
            "🕒 Não imediatos",
            "Demais itens (bebidas, doces, salgados frios…)",
            "#1769aa"
        )
        self.conteudo_layout.addWidget(self.secao_nao_imediatos["widget"])

        self.conteudo_layout.addStretch()

        scroll.setWidget(self.conteudo)
        layout.addWidget(scroll)

        # ---------- Auto-refresh ----------
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.atualizar)
        self.timer.start(4000)   # 4 segundos

        self.atualizar()

    # ------------------------------------------------------

    def _criar_secao(self, titulo, subtitulo, cor):
        widget = QFrame()
        widget.setStyleSheet("""
            QFrame {
                background: #fafcff;
                border: 1px solid #dbeafe;
                border-radius: 12px;
            }
        """)

        v = QVBoxLayout(widget)
        v.setContentsMargins(16, 14, 16, 16)
        v.setSpacing(10)

        cabecalho = QHBoxLayout()

        titulo_lbl = QLabel(titulo)
        titulo_lbl.setStyleSheet(
            f"font-size: 20px; font-weight: bold; color: {cor}; "
            "background: transparent; border: none;"
        )
        cabecalho.addWidget(titulo_lbl)
        cabecalho.addSpacing(10)

        sub_lbl = QLabel(subtitulo)
        sub_lbl.setStyleSheet(
            "font-size: 12px; color: #777; "
            "background: transparent; border: none;"
        )
        cabecalho.addWidget(sub_lbl)
        cabecalho.addStretch()

        contador = QLabel("0 comandas")
        contador.setStyleSheet(
            "font-size: 12px; color: #555; "
            "background: transparent; border: none;"
        )
        cabecalho.addWidget(contador)

        v.addLayout(cabecalho)

        grid_widget = QWidget()
        grid_widget.setStyleSheet("background: transparent; border: none;")
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        v.addWidget(grid_widget)

        vazio = QLabel("Nenhuma comanda pendente nesta categoria.")
        vazio.setAlignment(Qt.AlignCenter)
        vazio.setStyleSheet(
            "font-size: 14px; color: #999; padding: 30px; "
            "background: transparent; border: none;"
        )
        v.addWidget(vazio)

        return {
            "widget": widget,
            "grid": grid,
            "vazio": vazio,
            "contador": contador,
        }

    def _limpar_grid(self, grid):
        while grid.count():
            item = grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _preencher_secao(self, secao, comandas):
        grid = secao["grid"]
        self._limpar_grid(grid)

        if not comandas:
            secao["vazio"].setVisible(True)
            secao["contador"].setText("0 comandas")
            return

        secao["vazio"].setVisible(False)

        n = len(comandas)
        rotulo = "comanda" if n == 1 else "comandas"
        secao["contador"].setText(f"{n} {rotulo}")

        for i, comanda in enumerate(comandas):
            card = CardComanda(comanda, self.marcar_pronto)
            grid.addWidget(card, i // self.COLUNAS, i % self.COLUNAS)

    def marcar_pronto(self, comanda_id):
        resposta = QMessageBox.question(
            self,
            "Confirmar",
            "Marcar esta comanda como pronta e retirá-la do painel?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if resposta != QMessageBox.Yes:
            return

        remover_comanda_pendente(comanda_id)
        self.atualizar()

    def atualizar(self):
        comandas = carregar_comandas_pendentes()

        imediatas = [c for c in comandas if comanda_tem_imediato(c)]
        nao_imediatas = [c for c in comandas if not comanda_tem_imediato(c)]

        self._preencher_secao(self.secao_imediatos, imediatas)
        self._preencher_secao(self.secao_nao_imediatos, nao_imediatas)

        total = len(comandas)
        rotulo = "comanda" if total == 1 else "comandas"
        self.contador.setText(f"{total} {rotulo} pendente(s)")


def pedir_senha_admin(parent=None, tentativas=3):
    """Solicita a senha do administrador. Retorna True se acertar."""

    for restantes in range(tentativas, 0, -1):
        senha, ok = QInputDialog.getText(
            parent,
            "Acesso restrito",
            f"Digite a senha do administrador "
            f"({restantes} tentativa{'s' if restantes > 1 else ''}):"
        )

        if not ok:
            # Usuário cancelou
            return False

        if senha == ADMIN_SENHA:
            return True

        QMessageBox.warning(
            parent,
            "Senha incorreta",
            "A senha digitada está incorreta. Tente novamente."
        )

    return False

# ==========================================================
# Main
# ==========================================================

if __name__ == "__main__":
    app = QApplication(sys.argv)

    app.setStyleSheet("""
        QWidget {
            font-family: Arial;
        }

        QScrollBar:vertical {
            width: 9px;
            background: #e8f1fb;
            border: none;
        }

        QScrollBar::handle:vertical {
            background: #4d8edb;
            border-radius: 4px;
            min-height: 40px;
        }

        QLineEdit {
            border: 1px solid #a9c7e8;
            border-radius: 7px;
            padding: 8px;
        }

        QToolTip {
            background: #174ea6;
            color: white;
            border: none;
        }
    """)

    # Tela do cliente (totem) — sempre abre
    janela = Totem()
    janela.show()

    # Tela do administrador — protegida por senha
    if pedir_senha_admin(janela):
        admin = JanelaAdmin()
        admin.show()
    else:
        QMessageBox.information(
            janela,
            "Acesso negado",
            "O painel do administrador não será aberto."
        )

    sys.exit(app.exec())