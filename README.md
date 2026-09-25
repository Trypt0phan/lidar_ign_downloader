# LiDAR IGN Downloader

Plugin QGIS pour télécharger les dalles LiDAR HD IGN et produits dérivés depuis la Géoplateforme nationale.

![Aperçu du plugin](screenshot.png)

## Fonctionnalités

- Sélection du produit : MNT, MNS, MNH ou nuage de points classifié LiDAR HD, ou MNT RGE ALTI 1 m
- Définition de l'emprise par rectangle dessiné sur le canevas ou depuis la couche active
- Listage des dalles LiDAR HD disponibles via le service WFS IGN (couche `IGNF_LIDAR-HD_METADONNEE:metadata`)
- Affichage de la couche des dalles LiDAR HD dans le projet (bouton « Charger les dalles »)
- MNT RGE ALTI 1 m en dalles de 1 km (France métropolitaine), extraites du service WMS-R IGN
- Complément automatique du MNT LiDAR HD par le RGE ALTI 1 m sur les dalles pas encore diffusées
- Téléchargement en parallèle avec barre de progression et nouvelles tentatives en cas d'erreur du serveur
- Chargement automatique des rasters et nuages de points dans QGIS après téléchargement

## Prérequis

- QGIS 3.28 ou supérieur
- Connexion internet (accès à la Géoplateforme IGN)

## Installation

1. Télécharger le `.zip` du plugin
2. Dans QGIS : `Extensions → Installer/Gérer les extensions → Installer depuis un ZIP`
3. Sélectionner le fichier `.zip` et cliquer sur **Installer**

## Utilisation

1. Lancer le plugin via le menu `Extensions → LiDAR IGN Downloader` ou l'icône dans la barre d'outils
2. Choisir le produit souhaité
3. Définir un dossier de sortie
4. Définir l'emprise (rectangle ou couche active)
5. Cliquer **Lister les dalles** pour interroger l'IGN
6. Sélectionner les dalles à télécharger et cliquer **Télécharger**

