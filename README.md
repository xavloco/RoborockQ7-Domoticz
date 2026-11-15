# Plugin Domoticz Roborock Q7

Auteur : Xavier. Version 3.0.0.
Pilote un Roborock Q7 / Q7 Max avec sa base, en local quand le robot répond, sinon par le cloud Roborock.

## Prérequis
- Domoticz avec les plugins Python activés.
- Python 3.11 ou plus récent (`python3 --version`).
- Un compte Roborock (email) et le robot sur le même réseau.

## Installation
1. Créer le dossier : `mkdir -p ~/domoticz/plugins/RoborockQ7`
2. Copier `plugin.py` dans ce dossier.
3. Redémarrer Domoticz : `sudo systemctl restart domoticz`
4. Matériel > Ajouter, type « Roborock Q7 (local + cloud) », saisir l'email Roborock, puis Ajouter.
5. Au premier démarrage, le plugin crée son environnement Python (dossier `venv`) et installe python-roborock. Compter plusieurs minutes sur un RPi 3B+. Le suivi est dans le journal Domoticz.

## Première connexion (code par email)
1. Écrire `demander` dans le champ « Code email » du matériel, puis cliquer sur Mettre à jour. Un email part.
2. Attendre l'email. Remplacer `demander` par le code reçu (sans espace), puis cliquer sur Mettre à jour.
3. Le journal affiche « Connecté à <nom du robot> ». La session est enregistrée dans `roborock_user.json`, le code n'est plus redemandé.

Si la session expire : supprimer `roborock_user.json`, écrire `demander` et recommencer.

## Réglages du matériel
- Email Roborock.
- Code email : voir ci-dessus.
- Intervalle statut : en secondes (30 par défaut).
- Image carte (chemin) : par défaut `/home/pi/domoticz/www/roborock_map.png`. Adapter si ton dossier est différent.
- Nom du robot : facultatif, utile s'il y a plusieurs robots.
- Carte en direct pendant le nettoyage : en secondes (20 par défaut, 0 pour désactiver).

## Dispositifs créés
| Dispositif | Rôle |
|---|---|
| Action | Démarrer, pause, stop, retour base, localiser, vider le bac, arrêter la vidange |
| Aspiration, Eau | Puissance d'aspiration et débit d'eau |
| Pièces | Nettoyage d'une pièce de l'étage actif |
| Étage | Choix de la carte (rez-de-chaussée, étage...) |
| Cycles | 1 à 3 passages (pièce ou démarrage complet) |
| Vidange auto | Active ou désactive le vidage automatique du bac |
| Batterie, Statut | Niveau et état, avec l'erreur en clair |
| Durée, Surface | Nettoyage en cours ou dernier nettoyage |
| Nb nettoyages, Temps total, Surface totale | Historique cumulé |
| Dernier nettoyage | Date, durée, surface, terminé ou interrompu |
| Brosses, Filtre, Capteurs | Usure en % restant |
| Alerte | Erreur (rouge), base (orange), consommable (jaune), dernier message (vert) |
| Dernier message, Bilan nettoyage | Messages type application et bilan du nettoyage |
| Maj carte | Interrupteur pour régénérer la carte |

## Carte
- L'image est régénérée pendant le nettoyage, en fin de nettoyage, au changement d'étage et avec « Maj carte ». Le trajet est en rouge et la position du robot en vert.
- Image : `http://IP-DU-RPI:8080/roborock_map.png`
- Page qui se recharge toute seule : `http://IP-DU-RPI:8080/roborock.html`
- Si le dossier `www/templates` existe, la page `Roborock.html` apparaît dans le menu Personnalisé de Domoticz.

## Notifications sur téléphone
Le plugin ne notifie pas lui-même. Dans Domoticz, ouvrir le dispositif « Roborock Alerte » et ajouter une notification (Pushover, Telegram...).

## Fonctionnement
- Le robot est géré par un processus séparé (`plugin.py --worker`), ce qui évite les erreurs à l'arrêt de Domoticz. Il redémarre seul en cas de problème, et au-delà de 400 Mo de mémoire.
- Fichiers créés à côté de `plugin.py` : `venv/`, `roborock_user.json`, `roborock_login_id.txt`, `roborock_worker.log`.
- Mémoire de référence sur RPi 3B+ : environ 61 Mo au repos, 72 Mo après un nettoyage avec carte.

## Problèmes courants
- « Invalid code » : le code a été annulé par une nouvelle demande. Écrire `demander`, attendre l'email, puis saisir ce dernier code.
- Erreur -10007 « invalid status » au changement d'étage : le robot doit être arrêté, sur sa base et localisé.
- « Command timed out » : robot en veille ou Wi-Fi faible. Le plugin retente tout seul.
- Rien ne se passe : consulter le journal Domoticz et `roborock_worker.log`.

## Limites connues
- Les durées de vie des consommables sont des valeurs standard, à ajuster dans `LIFE_H` dans `plugin.py`.
- Les noms des erreurs viennent de la bibliothèque, en anglais.
- Les messages sont déduits de l'état du robot, ils ne reprennent pas tous les textes de l'application Roborock.
