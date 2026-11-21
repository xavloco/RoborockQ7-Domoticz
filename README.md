# Plugin Domoticz pour aspirateur robot Roborock Q7

Plugin Domoticz pour les aspirateurs robots Roborock Q7 / Q7 Max avec leur base. Il utilise la bibliothèque python-roborock : le robot est piloté en local quand il répond, avec le cloud Roborock en secours. Le compte Roborock sert à l'authentification, aux noms des pièces et des étages.

Projet non officiel, sans lien avec Roborock ni Domoticz. Testé sur un seul robot (Roborock Q7 avec base). Les boutons Démarrer, Pièces et Étage commandent un vrai robot : il se met en marche ou change de carte.

## Fonctionnalités

| N° | Appareil Domoticz | Rôle |
|----|-------------------|------|
| 1 | Roborock Action | Démarrer, Pause, Stop, Retour base, Localiser, Vider bac, Stop vidange |
| 2 | Roborock Aspiration | Silencieux, Équilibré, Turbo, Max |
| 3 | Roborock Eau | Coupée, Faible, Moyen, Fort |
| 4 | Roborock Batterie | Niveau en % (graphique Domoticz) |
| 5 | Roborock Statut | État du robot, avec l'erreur en clair |
| 6 | Roborock Durée | Durée du nettoyage en cours ou du dernier (min) |
| 7 | Roborock Surface | Surface nettoyée (m²) |
| 8 | Roborock Dernier nettoyage | Date, durée, surface, terminé ou interrompu |
| 9 | Roborock Nb nettoyages | Nombre total de nettoyages |
| 10 | Roborock Temps total | Temps de nettoyage cumulé (h) |
| 11 | Roborock Surface totale | Surface cumulée (m²) |
| 12 | Roborock Pieces | Nettoie une pièce de l'étage actif |
| 13 | Roborock Maj carte | Interrupteur : régénère l'image de la carte |
| 14 | Roborock Brosse principale | Usure en % restant |
| 15 | Roborock Brosse laterale | Usure en % restant |
| 16 | Roborock Filtre | Usure en % restant |
| 17 | Roborock Capteurs | Usure en % restant |
| 18 | Roborock Etage | Choix de la carte (rez-de-chaussée, étage...) |
| 19 | Roborock Vidange auto | Active ou désactive le vidage automatique du bac à la base |
| 20 | Roborock Cycles | Sélecteur de 1 à 3 passages |
| 21 | Roborock Alerte | Rouge : erreur du robot. Orange : problème de base. Jaune : consommable sous 10 %. Vert : dernier message |
| 22 | Roborock Dernier message | Dernier événement avec l'heure (retour à la station, erreur...) |
| 23 | Roborock Bilan nettoyage | Surface et durée du dernier nettoyage terminé |

Autres points :

- Connexion locale automatique quand le robot répond, sinon par le cloud Roborock.
- Le dialogue avec le robot tourne dans un processus Python séparé (`plugin.py --worker`) : il se ferme proprement quand Domoticz s'arrête, et il redémarre tout seul en cas de problème ou s'il dépasse 400 Mo de mémoire.
- Le plugin crée son propre environnement Python (dossier `venv`) et y installe python-roborock au premier démarrage.
- La carte est dessinée avec le trajet (rouge) et la position du robot (vert). Elle est régénérée pendant le nettoyage (toutes les 20 s par défaut), en fin de nettoyage, au changement d'étage et avec « Maj carte ».
- Protection des commandes : elles sont ignorées si le robot n'est pas connecté, et un refus du robot est journalisé sans bloquer le plugin.
- Messages déduits de l'état du robot : nettoyage démarré, terminé avec retour à la station, arrivée à la station, pause, vidage du bac, erreur et erreur résolue.
- Les étapes lentes (historique, consommables, pièces, étages) sont retentées en cas de timeout, un timeout persistant n'est qu'une information dans le journal.

## Prérequis

- Domoticz avec les plugins Python activés (testé avec Domoticz 2026.4 et Python 3.13 sur Raspberry Pi 3B+).
- Python 3.11 ou plus récent (`python3 --version`), avec `python3-venv` et `python3-pip`.
- Un compte Roborock (l'email) et le robot relié à l'application Roborock.
- Le robot et Domoticz sur le même réseau pour la connexion locale.

## 1. Première connexion (code par email)

Aucun compte développeur n'est nécessaire. La connexion se fait avec un code envoyé par email, une seule fois.

1. Ajoute le matériel (étape 3) avec ton email, en laissant le champ « Code email » vide.
2. Écris `demander` dans « Code email », puis clique sur Mettre à jour : un email part.
3. Attends l'email. Remplace `demander` par le code reçu (sans espace), puis clique sur Mettre à jour.
4. Le journal affiche « Connecte a <nom du robot> (local: True) ». La session est enregistrée dans `roborock_user.json` : le code n'est plus demandé.

Attention : chaque nouvelle demande de code annule le précédent. Ne relance rien entre la demande et la saisie. Si la session expire, supprime `roborock_user.json`, écris à nouveau `demander` et recommence.

Réserve aussi l'adresse IP du robot dans ta box (bail DHCP statique) pour garder une connexion locale stable.

## 2. Installation

Sur le Raspberry Pi de Domoticz (adapte le dossier des plugins) :

```
sudo apt install -y python3-pip python3-venv git
cd /home/pi/domoticz/plugins
git clone https://github.com/xavloco/RoborockQ7-Domoticz.git RoborockQ7
sudo systemctl restart domoticz
```

Le dossier du plugin doit être accessible en écriture à l'utilisateur qui lance Domoticz : le plugin y crée `venv/` et ses fichiers. Si Domoticz n'est pas un service (lancé à la main) :

```
sudo pkill domoticz; sleep 5; sudo /home/pi/domoticz/domoticz -daemon -www 8080 -sslwww 443
```

Dossiers de plugins courants : `/home/pi/domoticz/plugins`, `/opt/domoticz/plugins`, `/usr/share/domoticz/plugins`. Avec Docker, clone dans le dossier monté en volume pour les plugins.

Mise à jour :

```
cd /home/pi/domoticz/plugins/RoborockQ7 && git pull && sudo systemctl restart domoticz
```

Au premier démarrage, python-roborock s'installe automatiquement dans `venv/` (plusieurs minutes sur un Raspberry Pi 3B+, suivi dans le journal). Si ça échoue :

```
cd /home/pi/domoticz/plugins/RoborockQ7
python3 -m venv venv
venv/bin/python -m pip install --upgrade python-roborock
sudo systemctl restart domoticz
```

## 3. Ajouter le matériel dans Domoticz

Réglages > Matériel, type **Roborock Q7 (local + cloud)**.

| Champ | À renseigner |
|-------|--------------|
| Email Roborock | L'email de ton compte Roborock |
| Code email (première connexion) | `demander`, puis le code reçu par email (voir étape 1) |
| Intervalle statut | En secondes (30 par défaut) |
| Image carte (chemin) | `/home/pi/domoticz/www/roborock_map.png` par défaut : adapte-le à ton dossier `www` |
| Nom du robot | Facultatif, utile s'il y a plusieurs robots sur le compte |
| Carte en direct pendant le nettoyage | En secondes (20 par défaut, 0 pour désactiver) |

Clique sur Ajouter, puis ouvre Réglages > Journal : tu dois voir « Connecte a <nom du robot> (local: True) ». Les appareils se créent tout seuls. Si une fonction ne t'intéresse pas, supprime l'appareil à la main dans Domoticz.

## 4. Voir la carte

- Image seule : `http://IP-DU-RPI:8080/roborock_map.png`
- Page qui se recharge toute seule toutes les 10 s : `http://IP-DU-RPI:8080/roborock.html`
- Si le dossier `www/templates` existe, la page `Roborock.html` apparaît dans le menu Personnalisé de Domoticz après un rafraîchissement du navigateur (Ctrl+F5).

L'image seule ne se recharge pas toute seule : utilise la page pour suivre le robot.

## 5. Notifications sur téléphone

Le plugin ne notifie pas lui-même. Dans Domoticz, ouvre l'appareil « Roborock Alerte » et ajoute une notification (Pushover, Telegram...).

## 6. Comment ça marche

Commandes envoyées au robot :

| Appareil | Commande Roborock |
|----------|-------------------|
| Action : Démarrer | `app_start` (ou `app_segment_clean` sur toutes les pièces si Cycles est à 2 ou 3) |
| Action : Pause, Stop | `app_pause`, `app_stop` |
| Action : Retour base | `app_charge` |
| Action : Localiser | `find_me` |
| Action : Vider bac, Stop vidange | `app_start_collect_dust`, `app_stop_collect_dust` |
| Aspiration | `set_custom_mode` (101 à 104) |
| Eau | `set_water_box_custom_mode` (200 à 203) |
| Pièces | `app_segment_clean` avec le nombre de cycles choisi |
| Vidange auto | `set_dust_collection_switch_status` |
| Étage | Changement de carte du robot |

Rafraîchissement : l'état du robot est relu à chaque intervalle. L'historique, les consommables, les pièces et les étages sont relus toutes les 15 minutes.

Fichiers créés à côté de `plugin.py` : `venv/`, `roborock_user.json` (session), `roborock_login_id.txt`, `roborock_worker.log`. Ne les publie jamais.

Mémoire de référence sur Raspberry Pi 3B+ : environ 61 Mo au repos et 72 Mo après un nettoyage avec la carte.

## 7. Dépannage

Ouvre Réglages > Journal dans Domoticz. Le détail du processus robot est dans `roborock_worker.log`.

| Message | Que faire |
|---------|-----------|
| Invalid code | Le code a été annulé par une nouvelle demande, ou il a expiré. Écris `demander`, attends l'email, saisis ce dernier code |
| Aucune session enregistree | Écris `demander` dans « Code email » et clique sur Mettre à jour |
| Session expiree | Supprime `roborock_user.json`, écris `demander` et recommence |
| python-roborock trop ancien / Echec pip | Lance l'installation manuelle (étape 2) et vérifie Python 3.11 ou plus |
| `-10007 invalid status` au changement d'étage | Le robot doit être arrêté, sur sa base et localisé sur sa carte |
| Command timed out / robot injoignable | Robot en veille ou Wi-Fi faible. Le plugin retente tout seul. Vérifie le Wi-Fi à l'endroit de la base |
| Robot non connecte, commande ignoree | Le processus robot n'est pas connecté : attends sa reconnexion (nouvel essai toutes les 60 s) |
| Processus Roborock arrete | Voir `roborock_worker.log` |
| Rien dans la liste des types de matériel | Redémarre Domoticz et vérifie que les plugins Python sont activés |

## Limites connues

- Testé sur un seul modèle de robot. L'interrupteur « Vidange auto » et le nombre de cycles dépendent des commandes acceptées par le modèle.
- Les durées de vie des consommables sont des valeurs standard, à ajuster dans `LIFE_H` dans `plugin.py`.
- Les noms des erreurs viennent de la bibliothèque, en anglais, sans traduction (le code est dans le journal).
- Les messages sont déduits de l'état du robot : ils ne reprennent pas tous les textes de l'application Roborock.
- Le sélecteur « Pièces » concerne l'étage actif. Pour une autre pièce, change d'abord d'étage.
- Le changement d'étage n'est accepté que robot arrêté, sur sa base.
- Les noms de pièces et d'étages sont relus toutes les 15 minutes.


## Licence
GNU General Public License v3.0, voir LICENSE.

