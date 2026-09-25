# -*- coding: utf-8 -*-
import http.client
import math
import os
import re
import socket
import threading
import time
import urllib.request
import urllib.error
from urllib.parse import parse_qs, urlparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from qgis.PyQt.QtCore import Qt, QDate, QSize, QObject, QSettings, pyqtSignal
from qgis.PyQt.QtGui import QIcon, QColor
from qgis.PyQt.QtWidgets import (
    QToolButton,
    QApplication,
    QAction,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGridLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QFileDialog,
    QLineEdit,
    QTextEdit,
    QMessageBox,
    QCheckBox,
    QProgressBar,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QWidget,
    QGroupBox,
    QSplitter,
)

from qgis.core import (
    Qgis,
    QgsProject,
    QgsCoordinateReferenceSystem,
    QgsMapLayer,
    QgsVectorLayer,
    QgsFeatureRequest,
    QgsGeometry,
    QgsCoordinateTransform,
    QgsRasterLayer,
    QgsPointCloudLayer,
    QgsWkbTypes,
    QgsTask,
    QgsApplication,
    QgsRectangle,
    QgsPointXY,
    QgsFeature,
    QgsPalLayerSettings,
    QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
    NULL,
)

from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand


try:
    RUBBERBAND_GEOM_TYPE = QgsWkbTypes.PolygonGeometry
except Exception:
    RUBBERBAND_GEOM_TYPE = 2


class RectangleMapTool(QgsMapToolEmitPoint):
    rectangleCreated = pyqtSignal(QgsRectangle)
    drawingCancelled = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self.start_point = None
        self.end_point = None
        self.is_drawing = False

        self.rubber_band = QgsRubberBand(self.canvas, RUBBERBAND_GEOM_TYPE)
        self.rubber_band.setColor(QColor(255, 0, 0, 180))
        self.rubber_band.setWidth(2)
        self.rubber_band.setFillColor(QColor(255, 0, 0, 40))

    def reset(self):
        self.start_point = None
        self.end_point = None
        self.is_drawing = False
        self.rubber_band.reset(RUBBERBAND_GEOM_TYPE)

    def canvasPressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self.start_point = self.toMapCoordinates(event.pos())
        self.end_point = self.start_point
        self.is_drawing = True
        self._show_rect()

    def canvasMoveEvent(self, event):
        if not self.is_drawing:
            return
        self.end_point = self.toMapCoordinates(event.pos())
        self._show_rect()

    def canvasReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or not self.is_drawing:
            return

        self.end_point = self.toMapCoordinates(event.pos())
        self.is_drawing = False
        self._show_rect()

        rect = QgsRectangle(self.start_point, self.end_point)
        if rect.isEmpty():
            self.reset()
            self.drawingCancelled.emit()
            return

        self.rectangleCreated.emit(rect)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reset()
            self.drawingCancelled.emit()

    def _show_rect(self):
        self.rubber_band.reset(RUBBERBAND_GEOM_TYPE)
        if self.start_point is None or self.end_point is None:
            return

        p1 = QgsPointXY(self.start_point.x(), self.start_point.y())
        p2 = QgsPointXY(self.start_point.x(), self.end_point.y())
        p3 = QgsPointXY(self.end_point.x(), self.end_point.y())
        p4 = QgsPointXY(self.end_point.x(), self.start_point.y())

        self.rubber_band.addPoint(p1, False)
        self.rubber_band.addPoint(p2, False)
        self.rubber_band.addPoint(p3, False)
        self.rubber_band.addPoint(p4, False)
        self.rubber_band.addPoint(p1, True)
        self.rubber_band.show()


class LidarIgnDownloaderDialog(QDialog):
    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setWindowTitle("Téléchargement LiDAR IGN")
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.resize(1180, 780)

        root = QVBoxLayout(self)

        top_splitter = QSplitter(Qt.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        params_group = QGroupBox("Paramètres")
        params_form = QFormLayout(params_group)

        self.product_combo = QComboBox()
        self.product_combo.addItems(list(LidarIgnDownloaderPlugin.PRODUCTS))
        self.product_combo.insertSeparator(self.product_combo.count())
        self.product_combo.addItem(LidarIgnDownloaderPlugin.RGEALTI_LABEL)
        params_form.addRow("Produit", self.product_combo)

        out_row = QWidget()
        out_layout = QHBoxLayout(out_row)
        out_layout.setContentsMargins(0, 0, 0, 0)
        self.output_edit = QLineEdit()
        self.browse_button = QPushButton("Parcourir…")
        out_layout.addWidget(self.output_edit)
        out_layout.addWidget(self.browse_button)
        params_form.addRow("Dossier de sortie", out_row)

        self.workers_spinbox = QSpinBox()
        self.workers_spinbox.setMinimum(1)
        self.workers_spinbox.setMaximum(4)
        self.workers_spinbox.setValue(2)
        params_form.addRow("Téléchargements simultanés", self.workers_spinbox)

        left_layout.addWidget(params_group)

        extent_group = QGroupBox("Définir l'emprise")
        extent_layout = QVBoxLayout(extent_group)

        extent_grid = QGridLayout()
        extent_grid.setContentsMargins(0, 0, 0, 0)
        extent_grid.setHorizontalSpacing(8)
        extent_grid.setVerticalSpacing(4)

        self.draw_rect_button = QPushButton("Dessiner un rectangle")
        self.use_active_layer_button = QPushButton("Utiliser la couche active")
        self.clear_extent_button = QPushButton("Effacer l'emprise")

        extent_grid.addWidget(self.draw_rect_button, 0, 0)
        extent_grid.addWidget(self.use_active_layer_button, 0, 1)
        extent_grid.addWidget(self.clear_extent_button, 0, 2)

        self.selected_only_checkbox = QCheckBox("Utiliser seulement la sélection de la couche active")
        self.selected_only_checkbox.setChecked(True)
        extent_grid.addWidget(self.selected_only_checkbox, 1, 1, alignment=Qt.AlignHCenter)

        extent_grid.setColumnStretch(0, 1)
        extent_grid.setColumnStretch(1, 1)
        extent_grid.setColumnStretch(2, 1)

        extent_layout.addLayout(extent_grid)

        self.extent_status_edit = QLineEdit()
        self.extent_status_edit.setReadOnly(True)
        self.extent_status_edit.setPlaceholderText("Aucune emprise définie")
        extent_layout.addWidget(QLabel("Emprise active"))
        extent_layout.addWidget(self.extent_status_edit)

        left_layout.addWidget(extent_group)
        left_layout.addStretch()
        top_splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        # Couches de référence, en dehors du flux de travail : petits boutons carrés
        self.show_lidar_tiles_button = self.make_square_button(
            ":/images/themes/default/mActionAddWfsLayer.svg",
            "L",
            "Afficher les dalles LiDAR HD\n"
            "Ajoute la couche WFS IGN de toutes les dalles LiDAR HD diffusées.",
        )
        self.show_rgealti_tiles_button = self.make_square_button(
            ":/images/themes/default/algorithms/mAlgorithmCreateGrid.svg",
            "R",
            "Afficher les dalles RGE ALTI\n"
            "Crée une couche temporaire avec la grille des dalles RGE ALTI 1 m sur toute la métropole.",
        )
        tiles_row = QHBoxLayout()
        tiles_row.addStretch()
        tiles_row.addWidget(QLabel("Dalles :"))
        tiles_row.addWidget(self.show_lidar_tiles_button)
        tiles_row.addWidget(self.show_rgealti_tiles_button)
        right_layout.addLayout(tiles_row)

        actions_group = QGroupBox("Actions")
        actions_layout = QVBoxLayout(actions_group)

        self.list_button = QPushButton("1 - Lister les dalles")
        self.download_button = QPushButton("2 - Télécharger les données")
        self.auto_load_checkbox = QCheckBox("Charger les données après téléchargement")
        self.auto_load_checkbox.setChecked(True)
        self.cancel_button = QPushButton("Annuler")
        self.cancel_button.setEnabled(False)

        for btn in (self.list_button, self.download_button, self.cancel_button):
            btn.setMinimumHeight(34)

        actions_layout.addWidget(self.list_button)
        actions_layout.addWidget(self.download_button)
        actions_layout.addWidget(self.auto_load_checkbox)
        actions_layout.addStretch()
        actions_layout.addWidget(self.cancel_button)

        right_layout.addWidget(actions_group)
        right_layout.addStretch()
        top_splitter.addWidget(right_panel)

        top_splitter.setSizes([910, 210])
        root.addWidget(top_splitter)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Télécharger", "ID dalle", "Nom fichier", "URL", "Infos"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.reset_table_layout()
        root.addWidget(self.table)

        self.clear_table_button = QPushButton("Vider la liste")
        clear_row = QHBoxLayout()
        clear_row.addStretch()
        clear_row.addWidget(self.clear_table_button)
        root.addLayout(clear_row)

        root.addWidget(QLabel("Progression globale"))
        self.progress_global = QProgressBar()
        self.progress_global.setRange(0, 100)
        self.progress_global.setValue(0)
        root.addWidget(self.progress_global)

        root.addWidget(QLabel("Journal"))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        root.addWidget(self.log)

    def closeEvent(self, event):
        if self.plugin.current_task is not None:
            reply = QMessageBox.question(
                self,
                "Téléchargement en cours",
                "Un téléchargement est en cours. Voulez-vous l'annuler et fermer ?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self.plugin.current_task.cancel()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

    def make_square_button(self, icon_path, fallback_text, tooltip):
        button = QToolButton()
        icon = QIcon(icon_path)
        if icon.isNull():
            button.setText(fallback_text)
        else:
            button.setIcon(icon)
            button.setIconSize(QSize(20, 20))
        button.setFixedSize(30, 30)
        button.setToolTip(tooltip)
        return button

    def add_log(self, text):
        self.log.append(text)

    def reset_table_layout(self):
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Télécharger", "ID dalle", "Nom fichier", "URL", "Infos"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 95)
        self.table.setColumnWidth(1, 260)
        self.table.setColumnWidth(2, 300)


class DownloadSignals(QObject):
    log = pyqtSignal(str)
    progress_global = pyqtSignal(int)


class DownloadTask(QgsTask):
    def __init__(self, rows_to_download, out_dir, signals, max_workers=2):
        super().__init__("Téléchargement LiDAR IGN", QgsTask.CanCancel)
        self.rows_to_download = rows_to_download
        self.out_dir = out_dir
        self.signals = signals
        self.max_workers = max_workers
        self.downloaded_files = []
        self.success_count = 0
        self.error_count = 0
        self._lock = threading.Lock()

    def sanitize_filename(self, name):
        if not name:
            return "fichier_inconnu"
        return re.sub(r'[<>:"/\\|?*]+', "_", name)

    MAX_ATTEMPTS = 3
    # Codes HTTP transitoires, retentés quelle que soit l'URL
    RETRYABLE_HTTP_CODES = (429, 500, 502, 503, 504)
    # Le WMS-R de la Géoplateforme renvoie par intermittence un 400 « LayerNotDefined »
    WMS_R_MARKER = "/wms-r"

    def _remove_quietly(self, path):
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    def _fetch(self, url, tmp_path):
        """Télécharge url dans tmp_path. Retourne (statut, message) avec statut
        'ok', 'cancelled', 'retry' ou 'error'."""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "QGIS LiDAR IGN Downloader"})
            with urllib.request.urlopen(req, timeout=300) as response:
                content_type = (response.headers.get("Content-Type") or "").lower()
                if "xml" in content_type or "html" in content_type:
                    detail = response.read(500).decode("utf-8", "replace")
                    return ("retry", f"réponse non binaire du serveur : {detail[:200]}")

                expected = response.headers.get("Content-Length")
                received = 0
                chunk_size = 4 * 1024 * 1024
                with open(tmp_path, "wb") as f:
                    while True:
                        if self.isCanceled():
                            return ("cancelled", None)
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        received += len(chunk)

            # Une connexion coupée en plein transfert se termine sans erreur : on vérifie la taille
            if expected and expected.isdigit() and received != int(expected):
                return ("retry", f"transfert incomplet : {received} octets reçus sur {expected}")
            return ("ok", None)

        except urllib.error.HTTPError as e:
            retryable = e.code in self.RETRYABLE_HTTP_CODES or (e.code == 400 and self.WMS_R_MARKER in url)
            status = "retry" if retryable else "error"
            return (status, f"HTTP {e.code} : {e.reason}")
        except urllib.error.URLError as e:
            return ("retry", f"URL : {e}")
        except (TimeoutError, socket.timeout) as e:
            return ("retry", f"délai dépassé : {e}")
        except (ConnectionError, http.client.HTTPException) as e:
            # Connexion coupée par le serveur, y compris en plein transfert
            return ("retry", f"connexion interrompue : {e!r}")
        except Exception as e:
            return ("error", str(e))

    def _download_worker(self, item):
        tile_id, file_name, url = item

        if self.isCanceled():
            return ("cancelled", tile_id, file_name, None)

        if not url:
            return ("error", tile_id, file_name, "URL absente.")

        if not file_name:
            file_name = os.path.basename(url.split("?")[0]) or f"{tile_id}.bin"

        safe_file_name = self.sanitize_filename(file_name)
        out_path = os.path.join(self.out_dir, safe_file_name)
        tmp_path = out_path + ".part"

        message = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            self._remove_quietly(tmp_path)
            status, message = self._fetch(url, tmp_path)

            if status == "ok":
                self._remove_quietly(out_path)
                os.replace(tmp_path, out_path)
                return ("ok", tile_id, safe_file_name, out_path)

            self._remove_quietly(tmp_path)
            if status == "cancelled":
                return ("cancelled", tile_id, safe_file_name, None)
            if status == "error" or attempt == self.MAX_ATTEMPTS:
                break

            # Pause croissante avant nouvelle tentative, interrompue si annulation
            for _ in range(attempt * 4):
                if self.isCanceled():
                    return ("cancelled", tile_id, safe_file_name, None)
                time.sleep(0.5)

        return ("error", tile_id, safe_file_name, message)

    def run(self):
        total = len(self.rows_to_download)
        if total == 0:
            self.signals.log.emit("Aucun fichier à télécharger.")
            return True

        submitted_index = 0
        completed_count = 0
        futures = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while submitted_index < total and len(futures) < self.max_workers:
                item = self.rows_to_download[submitted_index]
                futures[executor.submit(self._download_worker, item)] = item
                submitted_index += 1

            while futures:
                if self.isCanceled():
                    self.signals.log.emit("Téléchargement annulé.")
                    for future in futures:
                        future.cancel()
                    return False

                done, _ = wait(list(futures.keys()), timeout=0.2, return_when=FIRST_COMPLETED)
                if not done:
                    continue

                for future in done:
                    item = futures.pop(future)

                    try:
                        status, tile_id, safe_file_name, payload = future.result()
                    except Exception as e:
                        status = "error"
                        tile_id = item[0]
                        safe_file_name = item[1] or item[0]
                        payload = str(e)

                    with self._lock:
                        if status == "ok":
                            self.success_count += 1
                            self.downloaded_files.append(payload)
                            self.signals.log.emit(f"OK -> {safe_file_name}")
                        elif status == "error":
                            self.error_count += 1
                            self.signals.log.emit(f"ERREUR {tile_id} : {payload}")
                        elif status == "cancelled":
                            self.signals.log.emit(f"Annulé : {tile_id}")
                            return False

                    completed_count += 1
                    progress = int((completed_count / total) * 100)
                    self.setProgress(progress)
                    self.signals.progress_global.emit(progress)

                    if submitted_index < total and not self.isCanceled():
                        next_item = self.rows_to_download[submitted_index]
                        futures[executor.submit(self._download_worker, next_item)] = next_item
                        submitted_index += 1

        return True

    def finished(self, result):
        pass


class LidarIgnDownloaderPlugin:
    WFS_URL = "https://data.geopf.fr/wfs/ows"

    # Index unique des dalles LiDAR HD : une entité par dalle, un champ URL par produit
    WFS_TYPENAME = "IGNF_LIDAR-HD_METADONNEE:metadata"

    PRODUCTS = {
        "MNT : Modèle Numérique de Terrain (50 cm)": "url_mnt",
        "MNS : Modèle Numérique de Surface (50 cm)": "url_mns",
        "MNH : Modèle Numérique de Hauteur (50 cm)": "url_mnh",
        "Nuage de points LIDAR classifié": "url_npl",
    }

    TILE_NAME_FIELD = "coordonnees_nw"

    # RGE ALTI 1 m : pas d'index de dalles, on génère une grille de 1 km en Lambert 93
    RGEALTI_LABEL = "MNT RGE ALTI 1 m (France métropolitaine)"
    RGEALTI_WMS_URL = "https://data.geopf.fr/wms-r"
    RGEALTI_LAYER = "RGEALTI-MNT_PYR-ZIP_FXX_LAMB93_WMS"
    RGEALTI_CRS = "EPSG:2154"
    TILE_SIZE_M = 1000
    # Rectangle englobant la métropole et la Corse en Lambert 93 (xmin, ymin, xmax, ymax)
    RGEALTI_FXX_EXTENT = (98000, 6045000, 1243000, 7111000)
    RGEALTI_RESOLUTION_M = 1

    INFO_FIELD_CANDIDATES = [
        "code_mission",
        "date_debut_acquisition",
        "date_fin_acquisition",
        "date_edition",
        "capteur",
        "systeme_planimetrique",
        "systeme_altimetrique",
    ]

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dlg = None
        self.current_task = None
        self.current_signals = None

        self.rect_tool = None
        self.previous_map_tool = None

        self.current_extent_geom = None
        self.current_extent_crs = None
        self.current_extent_label = "Aucune emprise définie"

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        self.action = QAction(QIcon(icon_path), "LiDAR IGN Downloader", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu("&LiDAR IGN Downloader", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action:
            self.iface.removePluginMenu("&LiDAR IGN Downloader", self.action)
            self.iface.removeToolBarIcon(self.action)

    def load_settings(self):
        s = QSettings()
        self.dlg.output_edit.setText(s.value("lidar_ign_downloader/output_dir", ""))
        self.dlg.workers_spinbox.setValue(int(s.value("lidar_ign_downloader/max_workers", 2)))

    def save_settings(self):
        s = QSettings()
        s.setValue("lidar_ign_downloader/output_dir", self.dlg.output_edit.text().strip())
        s.setValue("lidar_ign_downloader/max_workers", self.dlg.workers_spinbox.value())

    def run(self):
        self.dlg = LidarIgnDownloaderDialog(self, self.iface.mainWindow())
        self.update_extent_status()
        self.load_settings()

        self.dlg.browse_button.clicked.connect(self.choose_output_dir)
        self.dlg.use_active_layer_button.clicked.connect(self.use_active_layer_extent)
        self.dlg.draw_rect_button.clicked.connect(self.start_rectangle_drawing)
        self.dlg.clear_extent_button.clicked.connect(self.clear_extent)
        self.dlg.show_lidar_tiles_button.clicked.connect(self.show_lidar_tiles)
        self.dlg.show_rgealti_tiles_button.clicked.connect(self.show_rgealti_tiles)
        self.dlg.list_button.clicked.connect(self.list_tiles)
        self.dlg.download_button.clicked.connect(self.download_tiles)
        self.dlg.cancel_button.clicked.connect(self.cancel_download)
        self.dlg.clear_table_button.clicked.connect(self.clear_tiles)

        self.dlg.exec()

    def choose_output_dir(self):
        folder = QFileDialog.getExistingDirectory(self.dlg, "Choisir un dossier de sortie")
        if folder:
            self.dlg.output_edit.setText(folder)

    def update_extent_status(self):
        if self.dlg is not None:
            self.dlg.extent_status_edit.setText(self.current_extent_label)

    def get_active_vector_layer(self):
        layer = self.iface.activeLayer()
        if not layer:
            return None
        if layer.type() != QgsMapLayer.VectorLayer:
            return None
        geom_type = QgsWkbTypes.geometryType(layer.wkbType())
        if geom_type not in (QgsWkbTypes.PolygonGeometry, QgsWkbTypes.LineGeometry, QgsWkbTypes.PointGeometry):
            return None
        return layer

    def get_emprise_geometry_from_layer(self, layer):
        if layer is None:
            return None

        if self.dlg.selected_only_checkbox.isChecked() and layer.selectedFeatureCount() > 0:
            features = list(layer.selectedFeatures())
        else:
            features = list(layer.getFeatures())

        if not features:
            return None

        geoms = [f.geometry() for f in features if f.geometry() and not f.geometry().isEmpty()]
        if not geoms:
            return None

        return QgsGeometry.unaryUnion(geoms)

    def use_active_layer_extent(self):
        layer = self.get_active_vector_layer()
        if not layer:
            QMessageBox.warning(
                self.dlg,
                "Erreur",
                "Activer d'abord une couche vecteur dans QGIS."
            )
            return

        geom = self.get_emprise_geometry_from_layer(layer)
        if geom is None or geom.isEmpty():
            QMessageBox.warning(self.dlg, "Erreur", "Aucune géométrie valide dans la couche active.")
            return

        if self.dlg.selected_only_checkbox.isChecked() and layer.selectedFeatureCount() > 0:
            label = f"Couche active : {layer.name()} ({layer.selectedFeatureCount()} entité(s) sélectionnée(s))"
        else:
            label = f"Couche active : {layer.name()}"

        self.current_extent_geom = geom
        self.current_extent_crs = layer.crs()
        self.current_extent_label = label
        self.update_extent_status()
        self.dlg.add_log("Emprise définie à partir de la couche active.")

    def start_rectangle_drawing(self):
        canvas = self.iface.mapCanvas()

        if self.rect_tool is None:
            self.rect_tool = RectangleMapTool(canvas)
            self.rect_tool.rectangleCreated.connect(self.on_rectangle_created)
            self.rect_tool.drawingCancelled.connect(self.on_rectangle_cancelled)

        self.previous_map_tool = canvas.mapTool()
        self.rect_tool.reset()

        self.iface.messageBar().pushInfo(
            "LiDAR IGN Downloader",
            "Dessiner un rectangle dans le canevas. Appuyer sur Échap pour annuler."
        )
        self.dlg.add_log("Dessiner un rectangle dans le canevas. Appuyer sur Échap pour annuler.")

        self.dlg.hide()
        canvas.setMapTool(self.rect_tool)

    def on_rectangle_created(self, rect):
        self.current_extent_geom = QgsGeometry.fromRect(rect)
        self.current_extent_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        self.current_extent_label = (
            f"Rectangle : xmin={rect.xMinimum():.0f}, ymin={rect.yMinimum():.0f}, "
            f"xmax={rect.xMaximum():.0f}, ymax={rect.yMaximum():.0f}"
        )

        if self.rect_tool is not None:
            self.rect_tool.reset()

        self.update_extent_status()
        self.restore_previous_map_tool()

        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()
        self.dlg.add_log("Emprise définie à partir d'un rectangle dessiné.")

    def on_rectangle_cancelled(self):
        if self.rect_tool is not None:
            self.rect_tool.reset()
        self.restore_previous_map_tool()
        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()
        self.dlg.add_log("Dessin du rectangle annulé.")

    def restore_previous_map_tool(self):
        canvas = self.iface.mapCanvas()
        if self.previous_map_tool is not None:
            canvas.setMapTool(self.previous_map_tool)
        elif self.rect_tool is not None:
            canvas.unsetMapTool(self.rect_tool)

    def clear_extent(self):
        self.current_extent_geom = None
        self.current_extent_crs = None
        self.current_extent_label = "Aucune emprise définie"
        if self.rect_tool is not None:
            self.rect_tool.reset()
        self.update_extent_status()
        self.dlg.add_log("Emprise effacée.")

    def build_wfs_layer(self, typename):
        uri = (
            f"url={self.WFS_URL}"
            f" typename='{typename}'"
            f" version='2.0.0'"
            f" srsname='EPSG:2154'"
            f" pagingEnabled='true'"
            f" restrictToRequestBBOX='1'"
        )
        return QgsVectorLayer(uri, typename, "WFS")

    def style_tile_layer(self, layer, color, label_field):
        """Contour coloré sans remplissage et identifiant de dalle en étiquette."""
        symbol = layer.renderer().symbol()
        symbol.setColor(QColor(0, 0, 0, 0))
        symbol.symbolLayer(0).setStrokeColor(color)
        symbol.symbolLayer(0).setStrokeWidth(0.4)

        label_settings = QgsPalLayerSettings()
        label_settings.fieldName = label_field
        text_format = QgsTextFormat()
        text_format.setColor(color)
        text_format.setSize(8)
        label_settings.setFormat(text_format)
        # Afficher l'identifiant de chaque dalle même si les étiquettes se chevauchent
        label_settings.placementSettings().setOverlapHandling(Qgis.LabelOverlapHandling.AllowOverlapIfRequired)
        # Au-delà du 1:50 000 les étiquettes deviennent illisibles
        label_settings.scaleVisibility = True
        label_settings.minimumScale = 50000
        label_settings.maximumScale = 0
        layer.setLabeling(QgsVectorLayerSimpleLabeling(label_settings))
        layer.setLabelsEnabled(True)

    TILE_LAYER_PROPERTY = "lidar_ign_downloader/tile_layer"

    def replace_layer(self, layer, kind):
        """Ajoute la couche de dalles en remplaçant seulement celle du même type
        créée précédemment par le plugin, jamais une couche de l'utilisateur."""
        project = QgsProject.instance()
        previous = [
            l.id() for l in project.mapLayers().values()
            if l.customProperty(self.TILE_LAYER_PROPERTY) == kind
        ]
        project.removeMapLayers(previous)
        layer.setCustomProperty(self.TILE_LAYER_PROPERTY, kind)
        project.addMapLayer(layer)

    def show_lidar_tiles(self):
        layer = self.build_wfs_layer(self.WFS_TYPENAME)
        if not layer.isValid():
            self.dlg.add_log("Impossible de charger la couche des dalles LiDAR HD.")
            QMessageBox.critical(self.dlg, "Erreur WFS", "Impossible de charger la couche des dalles IGN.")
            return

        layer.setName("Dalles LiDAR HD")
        self.style_tile_layer(layer, QColor(230, 90, 20), self.TILE_NAME_FIELD)
        self.replace_layer(layer, "lidar")
        self.dlg.add_log("Couche WFS des dalles LiDAR HD ajoutée (toutes les dalles diffusées par l'IGN).")

    def show_rgealti_tiles(self):
        try:
            import processing
        except ImportError:
            self.show_grid_error(
                "L'extension « Processing » (Traitements) est nécessaire pour générer la grille.\n\n"
                "Activez-la dans Extensions → Installer/Gérer les extensions → Installées."
            )
            return

        self.dlg.add_log("Génération de la grille RGE ALTI sur toute la métropole...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            xmin, ymin, xmax, ymax = self.RGEALTI_FXX_EXTENT
            grid = processing.run("native:creategrid", {
                "TYPE": 2,  # rectangles
                "EXTENT": f"{xmin},{xmax},{ymin},{ymax} [{self.RGEALTI_CRS}]",
                "HSPACING": self.TILE_SIZE_M,
                "VSPACING": self.TILE_SIZE_M,
                "HOVERLAY": 0,
                "VOVERLAY": 0,
                "CRS": QgsCoordinateReferenceSystem(self.RGEALTI_CRS),
                "OUTPUT": "TEMPORARY_OUTPUT",
            })["OUTPUT"]
            # Même identifiant que le LiDAR HD : coin nord-ouest en km
            size_m = self.TILE_SIZE_M
            layer = processing.run("native:fieldcalculator", {
                "INPUT": grid,
                "FIELD_NAME": "id_dalle",
                "FIELD_TYPE": 2,  # texte
                "FIELD_LENGTH": 9,
                "FORMULA": (
                    f"lpad(to_string(round(\"left\" / {size_m})), 4, '0') || '-' || "
                    f"lpad(to_string(round(\"top\" / {size_m})), 4, '0')"
                ),
                "OUTPUT": "TEMPORARY_OUTPUT",
            })["OUTPUT"]
        except Exception as e:
            # Algorithme introuvable, mémoire insuffisante...
            self.show_grid_error(f"La génération de la grille a échoué.\n\nDétail : {e}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        layer.setName("Dalles RGE ALTI 1 m")
        self.style_tile_layer(layer, QColor(30, 110, 200), "id_dalle")
        self.replace_layer(layer, "rgealti")
        self.dlg.add_log(f"Grille RGE ALTI ajoutée : {layer.featureCount()} dalle(s) de 1 km sur la métropole.")

    def show_grid_error(self, message):
        self.dlg.add_log("Grille RGE ALTI non générée : " + message.splitlines()[0])
        QMessageBox.critical(self.dlg, "Grille RGE ALTI", message)

    def transform_geometry(self, geom, src_crs, dest_crs):
        if src_crs == dest_crs:
            return QgsGeometry(geom)
        g = QgsGeometry(geom)
        tr = QgsCoordinateTransform(src_crs, dest_crs, QgsProject.instance())
        g.transform(tr)
        return g

    def get_attr(self, feat, fields, fname):
        if not fname:
            return ""
        idx = fields.indexOf(fname)
        if idx < 0:
            return ""
        val = feat[idx]
        if val is None or val == NULL:
            return ""
        if isinstance(val, QDate):
            return val.toString("yyyy-MM-dd")
        return str(val)

    @staticmethod
    def file_name_from_url(url):
        # Les URL WMS-R portent le nom de fichier dans le paramètre FILENAME
        params = parse_qs(urlparse(url).query)
        for key, values in params.items():
            if key.upper() == "FILENAME" and values:
                return values[0]
        return os.path.basename(urlparse(url).path)

    def set_ui_busy(self, busy):
        for w in (
            self.dlg.product_combo,
            self.dlg.output_edit,
            self.dlg.browse_button,
            self.dlg.use_active_layer_button,
            self.dlg.selected_only_checkbox,
            self.dlg.draw_rect_button,
            self.dlg.clear_extent_button,
            self.dlg.show_lidar_tiles_button,
            self.dlg.show_rgealti_tiles_button,
            self.dlg.list_button,
            self.dlg.download_button,
            self.dlg.clear_table_button,
            self.dlg.auto_load_checkbox,
            self.dlg.workers_spinbox,
        ):
            w.setEnabled(not busy)
        self.dlg.cancel_button.setEnabled(busy)

    def cancel_download(self):
        if self.current_task:
            self.dlg.add_log("Annulation demandée...")
            self.current_task.cancel()

    def show_no_data_message(self, product_label):
        message = (
            f"Aucune dalle disponible pour le produit '{product_label}' sur l'emprise active.\n\n"
            "L'IGN n'a probablement pas encore diffusé cette donnée sur cette zone."
        )
        self.dlg.add_log(
            f"Aucune dalle disponible pour {product_label} sur la zone demandée. "
            "L'IGN n'a probablement pas encore diffusé cette donnée sur cette zone."
        )
        QMessageBox.information(self.dlg, "Aucune dalle disponible", message)

    def show_empty_table_message(self):
        self.dlg.table.setColumnCount(1)
        self.dlg.table.setRowCount(1)
        self.dlg.table.setHorizontalHeaderLabels(["Information"])
        self.dlg.table.setItem(
            0,
            0,
            QTableWidgetItem("Aucune donnée IGN disponible sur cette emprise pour ce produit")
        )
        self.dlg.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)

    def grid_tile_ids(self, emprise_l93):
        """Identifiants 'XXXX-YYYY' (coin nord-ouest en km, comme le LiDAR HD)
        des dalles de 1 km recoupées par l'emprise (polygone, ligne ou point)."""
        size = self.TILE_SIZE_M
        bbox = emprise_l93.boundingBox()

        engine = QgsGeometry.createGeometryEngine(emprise_l93.constGet())
        engine.prepareGeometry()
        # Pour un polygone, on ignore les dalles voisines qui ne font que toucher
        # son contour ; un point ou une ligne peut, lui, être posé sur un bord de dalle
        is_polygon = emprise_l93.type() == QgsWkbTypes.PolygonGeometry

        ids = []
        for x in range(math.floor(bbox.xMinimum() / size), math.floor(bbox.xMaximum() / size) + 1):
            for y in range(math.floor(bbox.yMinimum() / size), math.floor(bbox.yMaximum() / size) + 1):
                tile = QgsGeometry.fromRect(QgsRectangle(x * size, y * size, (x + 1) * size, (y + 1) * size))
                if not engine.intersects(tile.constGet()):
                    continue
                if is_polygon and engine.touches(tile.constGet()):
                    continue
                ids.append(f"{x:04d}-{y + 1:04d}")
        return ids

    def rgealti_row(self, tile_id):
        x_km, top_km = (int(v) for v in tile_id.split("-"))
        size = self.TILE_SIZE_M
        # Décalage d'un demi-pixel pour rester sur la grille native (centres de pixels entiers)
        half = self.RGEALTI_RESOLUTION_M / 2
        xmin = x_km * size - half
        ymin = (top_km - 1) * size - half
        pixels = size // self.RGEALTI_RESOLUTION_M
        file_name = f"RGEALTI_FXX_{x_km:04d}_{top_km:04d}_MNT_1M_LAMB93_IGN69.tif"
        url = (
            f"{self.RGEALTI_WMS_URL}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap"
            f"&LAYERS={self.RGEALTI_LAYER}&STYLES=&FORMAT=image/geotiff"
            f"&CRS={self.RGEALTI_CRS}&BBOX={xmin},{ymin},{xmin + size},{ymin + size}"
            f"&WIDTH={pixels}&HEIGHT={pixels}&FILENAME={file_name}"
        )
        return (tile_id, file_name, url, "source=RGE ALTI 1 m")

    def lidar_rows(self, product_label, url_field, emprise_l93):
        """Dalles LiDAR HD disponibles pour ce produit sur l'emprise, ou None si le WFS est en erreur."""
        self.dlg.add_log("Chargement de l'index IGN...")

        wfs_layer = self.build_wfs_layer(self.WFS_TYPENAME)
        if not wfs_layer.isValid():
            err = "Couche WFS invalide."
            try:
                if hasattr(wfs_layer, "error") and wfs_layer.error():
                    err = wfs_layer.error().message()
            except Exception:
                pass
            self.dlg.add_log(f"Erreur provider : {err}")
            QMessageBox.critical(
                self.dlg,
                "Erreur WFS",
                "Impossible de charger la couche WFS IGN.\n\n" f"Détail : {err}"
            )
            return None

        emprise_wfs = self.transform_geometry(emprise_l93, QgsCoordinateReferenceSystem(self.RGEALTI_CRS), wfs_layer.crs())

        fields = wfs_layer.fields()
        field_names = [f.name() for f in fields]
        missing = [f for f in (self.TILE_NAME_FIELD, url_field) if f not in field_names]
        if missing:
            self.dlg.add_log("Champs manquants : " + ", ".join(missing))
            QMessageBox.critical(
                self.dlg,
                "Champs manquants",
                "La couche WFS ne contient pas les champs attendus : " + ", ".join(missing)
            )
            return None

        self.dlg.add_log("Recherche des dalles intersectant l'emprise active...")

        rows = []
        req = QgsFeatureRequest().setFilterRect(emprise_wfs.boundingBox())
        for feat in wfs_layer.getFeatures(req):
            geom = feat.geometry()
            if not geom or geom.isEmpty() or not geom.intersects(emprise_wfs):
                continue
            # Dalle sans URL pour ce produit : donnée pas encore diffusée
            download_url = self.get_attr(feat, fields, url_field)
            if not download_url:
                continue

            tile_name = self.get_attr(feat, fields, self.TILE_NAME_FIELD) or f"feature_{len(rows) + 1}"
            infos = ["source=LiDAR HD"]
            for fname in self.INFO_FIELD_CANDIDATES:
                if fname in field_names:
                    v = self.get_attr(feat, fields, fname)
                    if v:
                        infos.append(f"{fname}={v}")
            rows.append((tile_name, self.file_name_from_url(download_url), download_url, " | ".join(infos)))

        self.dlg.add_log(f"Dalles LiDAR HD trouvées : {len(rows)}")
        return rows

    def fill_table(self, rows):
        self.dlg.reset_table_layout()
        self.dlg.table.setRowCount(len(rows))
        for row, (tile_name, file_name, download_url, info_text) in enumerate(rows):
            item_check = QTableWidgetItem()
            item_check.setCheckState(Qt.Checked)
            item_check.setTextAlignment(Qt.AlignCenter)
            item_check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)

            self.dlg.table.setItem(row, 0, item_check)
            self.dlg.table.setItem(row, 1, QTableWidgetItem(tile_name))
            self.dlg.table.setItem(row, 2, QTableWidgetItem(file_name))
            self.dlg.table.setItem(row, 3, QTableWidgetItem(download_url))
            self.dlg.table.setItem(row, 4, QTableWidgetItem(info_text))

    def clear_tiles(self):
        self.dlg.table.setRowCount(0)
        self.dlg.reset_table_layout()
        self.dlg.progress_global.setValue(0)
        self.dlg.add_log("Liste des dalles vidée.")

    def list_tiles(self):
        self.dlg.table.setRowCount(0)
        self.dlg.reset_table_layout()
        self.dlg.progress_global.setValue(0)
        self.dlg.log.clear()

        if self.current_extent_geom is None or self.current_extent_geom.isEmpty() or self.current_extent_crs is None:
            QMessageBox.warning(self.dlg, "Erreur", "Définir d'abord une emprise active.")
            return

        product_label = self.dlg.product_combo.currentText()
        self.dlg.add_log(f"Produit choisi : {product_label}")

        emprise_l93 = self.transform_geometry(
            self.current_extent_geom, self.current_extent_crs, QgsCoordinateReferenceSystem(self.RGEALTI_CRS)
        )

        if product_label == self.RGEALTI_LABEL:
            rows = [self.rgealti_row(tile_id) for tile_id in self.grid_tile_ids(emprise_l93)]
            self.dlg.add_log(f"Dalles RGE ALTI de 1 km sur l'emprise : {len(rows)}")
        else:
            rows = self.lidar_rows(product_label, self.PRODUCTS[product_label], emprise_l93)
            if rows is None:
                return

        if not rows:
            self.show_no_data_message(product_label)
            self.show_empty_table_message()
            return

        self.fill_table(rows)
        self.dlg.add_log(f"Liste prête : {len(rows)} dalle(s).")

    def load_file_if_needed(self, file_path):
        name = os.path.basename(file_path)
        lower = name.lower()
        if lower.endswith((".tif", ".tiff")):
            layer, kind = QgsRasterLayer(file_path, name), "Raster"
        elif lower.endswith(".copc.laz"):
            layer, kind = QgsPointCloudLayer(file_path, name, "copc"), "Nuage de points"
        elif lower.endswith((".laz", ".las")):
            layer, kind = QgsPointCloudLayer(file_path, name, "pdal"), "Nuage de points"
        else:
            return

        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            if self.dlg is not None:
                self.dlg.add_log(f"{kind} ajouté au canevas : {name}")
        elif self.dlg is not None:
            self.dlg.add_log(f"{kind} invalide : {file_path}")

    def on_task_log(self, text):
        if self.dlg is not None:
            self.dlg.add_log(text)

    def on_task_progress_global(self, value):
        if self.dlg is not None:
            self.dlg.progress_global.setValue(value)

    def on_task_completed(self):
        task = self.current_task
        if task is None:
            return

        self.set_ui_busy(False)
        self.dlg.progress_global.setValue(100)
        self.dlg.add_log(f"Terminé : {task.success_count} succès, {task.error_count} erreur(s).")

        if self.dlg.auto_load_checkbox.isChecked():
            self.dlg.add_log("Chargement des données téléchargées dans QGIS...")
            for file_path in task.downloaded_files:
                self.load_file_if_needed(file_path)
        else:
            self.dlg.add_log("Chargement automatique non activé.")

        QMessageBox.information(
            self.dlg,
            "Terminé",
            f"Nombre de téléchargements réussis : {task.success_count}/{len(task.rows_to_download)}"
        )

        self.current_task = None
        self.current_signals = None

    def on_task_terminated(self):
        task = self.current_task
        self.set_ui_busy(False)

        if task is not None:
            self.dlg.add_log("Téléchargement interrompu.")
            self.dlg.add_log(f"Partiel : {task.success_count} succès, {task.error_count} erreur(s).")

        QMessageBox.warning(
            self.dlg,
            "Téléchargement interrompu",
            "La tâche de téléchargement a été interrompue ou annulée."
        )

        self.current_task = None
        self.current_signals = None

    def download_tiles(self):
        if self.current_task is not None:
            QMessageBox.information(self.dlg, "Info", "Un téléchargement est déjà en cours.")
            return

        out_dir = self.dlg.output_edit.text().strip()
        if not out_dir:
            QMessageBox.warning(self.dlg, "Erreur", "Choisir un dossier de sortie.")
            return
        if not os.path.isdir(out_dir):
            QMessageBox.warning(self.dlg, "Erreur", "Vérifier que le dossier de sortie existe.")
            return
        if self.dlg.table.columnCount() < 4:
            QMessageBox.information(self.dlg, "Info", "Aucune dalle sélectionnable à télécharger.")
            return

        rows_to_download = []
        for row in range(self.dlg.table.rowCount()):
            check_item = self.dlg.table.item(row, 0)
            if check_item and check_item.checkState() == Qt.Checked:
                tile_id = self.dlg.table.item(row, 1).text() if self.dlg.table.item(row, 1) else f"feature_{row + 1}"
                file_name = self.dlg.table.item(row, 2).text() if self.dlg.table.item(row, 2) else ""
                url = self.dlg.table.item(row, 3).text() if self.dlg.table.item(row, 3) else ""
                rows_to_download.append((tile_id, file_name, url))

        if not rows_to_download:
            QMessageBox.information(self.dlg, "Info", "Aucune dalle sélectionnée.")
            return

        if len(rows_to_download) > 10:
            reply = QMessageBox.question(
                self.dlg,
                "Volume important",
                f"{len(rows_to_download)} dalles sélectionnées. Le téléchargement peut être long. Continuer ?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self.save_settings()
        self.dlg.progress_global.setValue(0)
        workers = self.dlg.workers_spinbox.value()
        self.dlg.add_log(
            f"Lancement du téléchargement en arrière-plan ({len(rows_to_download)} fichier(s), {workers} flux)..."
        )
        self.set_ui_busy(True)

        self.current_signals = DownloadSignals()
        self.current_signals.log.connect(self.on_task_log)
        self.current_signals.progress_global.connect(self.on_task_progress_global)

        self.current_task = DownloadTask(
            rows_to_download=rows_to_download,
            out_dir=out_dir,
            signals=self.current_signals,
            max_workers=self.dlg.workers_spinbox.value(),
        )
        self.current_task.taskCompleted.connect(self.on_task_completed)
        self.current_task.taskTerminated.connect(self.on_task_terminated)

        QgsApplication.taskManager().addTask(self.current_task)
