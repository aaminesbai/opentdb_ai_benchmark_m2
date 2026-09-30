# Benchmark LLM - Open Trivia Database

Le Bronze contient les questions brutes OpenTDB. Le pipeline Silver nettoie ces
questions, interroge un modèle local et conserve ses réponses et leur évaluation.

## Architecture du projet

Le projet suit une architecture en médaillon (Bronze → Silver → Gold) :

```
trivial-poursuite/
├── data/
│   ├── bronze/
│   │   └── questions_raw.csv          # Questions brutes OpenTDB
│   ├── silver/
│   │   ├── questions_enriched.parquet         # Questions nettoyées + réponses IA
│   │   └── questions_enriched_partial.parquet # Checkpoint de reprise
│   └── gold/
│       └── gold.duckdb                # Base DuckDB des analyses métier
├── scripts/
│   ├── scrape_opentdb.py              # Scraping OpenTDB (Bronze)
│   ├── api_count.py                   # Vérification du nombre de questions par catégorie
│   └── enrich_with_llm.py             # Enrichissement via LM Studio (Silver)
├── models/                            # Modèles dbt (Gold)
│   ├── stg_questions_enriched.sql
│   ├── gold_accuracy_overall.sql
│   ├── gold_accuracy_by_category.sql
│   ├── gold_accuracy_by_difficulty.sql
│   ├── gold_hardest_questions.sql
│   └── gold_error_rate.sql
├── tests/
│   └── test_enrich_with_llm.py        # Tests unitaires (sans appel au modèle)
├── streamlit_app.py                   # Dashboard interactif (rapport final)
├── dbt_project.yml
├── profiles.yml                       # Configuration de connexion dbt → DuckDB
├── requirements.txt
└── README.md
```

## Constitution du Bronze avec OpenTDB

Le script `scripts/scrape_opentdb.py` récupère l'intégralité des questions
disponibles sur [Open Trivia Database](https://opentdb.com) et les enregistre
dans `data/bronze/questions_raw.csv`.

Pour chaque catégorie, le nombre exact de questions disponibles est d'abord
obtenu via `api_count.php`, puis les questions sont récupérées par paquets de
50 maximum avec un jeton de session (`api_token.php`), afin d'éviter les
doublons entre appels. Le rythme des requêtes respecte la limite d'une requête
toutes les 5,2 secondes imposée par OpenTDB ; un code retour `5` (limite de
débit atteinte) déclenche une nouvelle tentative automatique.

Chaque ligne du Bronze conserve, en plus des champs natifs d'OpenTDB
(catégorie, type, difficulté, question, bonne réponse, mauvaises réponses) :

- `source` : toujours `OpenTDB` ;
- `category_id` : l'identifiant numérique de la catégorie ;
- `fetched_at` : l'horodatage UTC de récupération.

```powershell
python scripts/scrape_opentdb.py
```

Le script `scripts/api_count.py` permet, indépendamment, de vérifier le
nombre de questions disponibles par catégorie sur OpenTDB — utile pour
contrôler que le Bronze est complet.

## Enrichissement avec LM Studio

1. Installer [LM Studio](https://lmstudio.ai/).
2. Télécharger et charger Ministral.
3. Aller dans **Developer** et démarrer le serveur local sur le port **1234**.
4. Vérifier que `http://localhost:1234/v1/models` répond.
5. Depuis la racine du projet, installer les dépendances (Python 3.10 ou supérieur) :

   ```powershell
   python -m pip install -r requirements.txt
   ```

6. Tester sur 20 questions :

   ```powershell
   python scripts/enrich_with_llm.py --limit 20
   ```

7. Lancer le benchmark complet, en reprenant les questions restantes :

   ```powershell
   python scripts/enrich_with_llm.py
   ```

Le résultat est `data/silver/questions_enriched.parquet`. Les chemins de données
sont calculés depuis le fichier du script : on peut lancer celui-ci par son chemin
depuis un autre répertoire.

Le Bronze se trouve dans `data/bronze/questions_raw.csv`, à la racine du projet.
Les deux scripts utilisent ce chemin quel que soit le répertoire de lancement.
Le pipeline Silver ne modifie jamais le contenu du Bronze.

### Configuration et reprise

`MODEL = None` détecte le premier modèle de conversation retourné par `/v1/models`
(les identifiants contenant `embed` sont exclus). L'identifiant utilisé est affiché.
On peut le forcer avec `--model mistralai/ministral-3-3b` ou la constante `MODEL`.
Selon la configuration de chargement à la demande de LM Studio, `/v1/models` peut
aussi exposer des modèles disponibles mais non chargés : charger Ministral avant
le test reste recommandé. Voir la [documentation API LM Studio](https://lmstudio.ai/docs/developer/openai-compat).

`LIMIT = None` traite toutes les questions ; `--limit 20` vise les 20 premières,
y compris celles déjà traitées. Le checkpoint est enregistré toutes les 10
questions et à la sortie, y compris lors d'un Ctrl+C. Une fermeture forcée peut
perdre au maximum les neuf dernières réponses depuis la sauvegarde précédente.
Une question interrompue pendant sa requête sera rejouée.

Le fichier `data/silver/questions_enriched_partial.parquet` permet une reprise
automatique ; à défaut, le résultat final sert de checkpoint. Le partiel est conservé
après la fin. Les résultats précédents restent dans le Silver lorsqu'un test plus
petit est relancé. Le résumé console porte sur les questions visées par la commande.

```powershell
# Retenter uniquement les erreurs, et traiter les questions restantes
python scripts/enrich_with_llm.py --retry-errors

# Ignorer les anciens résultats et reconstruire le benchmark
python scripts/enrich_with_llm.py --restart
```

**`--restart` remplace le checkpoint et le résultat Silver par la nouvelle
exécution.** Copier ces fichiers avant cette commande pour garder un ancien
benchmark. Une reprise avec un autre modèle, une autre version de prompt ou des
paramètres différents est refusée pour éviter de mélanger les mesures.

### Nettoyage et protocole

- Les entités HTML sont décodées, les listes sont lues avec `json.loads()`.
  Le repli `ast.literal_eval()` accepte une liste Python et consigne un avertissement.
  Une liste irrécupérable produit une ligne en erreur sans requête au modèle.
- Un SHA-256 basé sur catégorie, question, bonne réponse et distracteurs identifie
  chaque question. Les doublons identiques sont signalés et évalués une seule fois.
  Ce hash sert aussi de seed pour mélanger les choix de façon reproductible.
- La température à 0 réduit la variabilité. Elle ne garantit pas une identité
  absolue entre matériels ou versions du moteur. Aucun seed serveur non vérifié
  n'est envoyé ; la limite de génération est de 50 tokens.
- Le prompt standardisé `v1_exact_choice` demande uniquement la réponse choisie.
  Le prompt exact et les choix sont stockés pour rendre l'évaluation vérifiable.
- `normalize_answer()` ignore les entités HTML, la casse, les espaces aux extrémités
  et un point final. Il n'utilise aucun rapprochement approximatif.
- Le temps réel de chaque requête est mesuré avec `time.perf_counter()`, afin de
  comparer aussi les latences. Les temps moyen et médian affichés concernent les succès.
- Les erreurs HTTP, timeouts (300 secondes), JSON invalides, réponses vides ou
  tronquées sont conservés avec `status=error` et `ai_correct=null`. Les autres
  questions continuent. Une réponse textuelle hors choix est une réponse incorrecte.
  L'accuracy exclut les erreurs. Un succès signifie une réponse exploitable,
  pas nécessairement une bonne réponse.
- Parquet conserve les types (notamment le booléen nullable `ai_correct`) et offre
  un stockage compact pour les futures analyses. Le Bronze reste la source brute
  inchangée ; les colonnes sources et les valeurs nettoyées coexistent en Silver.

Les requêtes passent uniquement par `requests` vers le serveur local, sans clé API,
sans SDK OpenAI, sans proxy système et sans suivre les redirections.

## Couche Gold avec dbt et DuckDB

dbt lit le fichier Silver `data/silver/questions_enriched.parquet` et construit
les vues d'analyse Gold dans une base DuckDB locale. Les modèles SQL se trouvent
dans `models/` :

- `stg_questions_enriched` : lecture de la couche Silver ;
- `gold_accuracy_overall` : précision et latence globales ;
- `gold_accuracy_by_category` : métriques par catégorie et difficulté ;
- `gold_accuracy_by_difficulty` : métriques par difficulté ;
- `gold_hardest_questions` : questions pour lesquelles le modèle s'est trompé,
  avec la réponse attendue et la réponse donnée, utile pour l'analyse
  qualitative des erreurs ;
- `gold_error_rate` : taux d'erreurs techniques (timeout, réponse invalide...)
  par catégorie, distinct du taux de mauvaises réponses du modèle.

Créer un fichier `profiles.yml` à la racine du projet avec la configuration
suivante :

```yaml
trivial_gold:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: "data/gold/gold.duckdb"
      threads: 4
```

Depuis la racine du projet, vérifier la configuration puis construire la couche
Gold :

```powershell
.\.venv\Scripts\dbt.exe debug --profiles-dir .
.\.venv\Scripts\dbt.exe run --profiles-dir .
```

La commande `dbt run` crée ou met à jour `data/gold/gold.duckdb`. Elle doit être
lancée après la génération du fichier Silver.

## Rapport de benchmark avec Streamlit

Le tableau de bord analyse la précision globale, les performances par catégorie,
difficulté et type de question, ainsi que la distribution des temps de réponse et
les erreurs techniques. Une vue de comparaison s'active automatiquement lorsque
plusieurs modèles sont présents dans les données.

Après avoir construit la couche Gold, lancer l'application depuis la racine :

```powershell
python -m pip install -r requirements.txt
.\.venv\Scripts\streamlit.exe run streamlit_app.py
```

Streamlit ouvre ensuite le rapport dans le navigateur. Les filtres de la barre
latérale permettent de limiter l'analyse à certains modèles, niveaux de difficulté
ou catégories.

### Environnement Windows préparé et vérifications

Un environnement `.venv` a été créé localement. Si `python` n'est pas dans le PATH,
utiliser directement les commandes suivantes, depuis la racine du projet :

```powershell
.\.venv\Scripts\python.exe scripts\enrich_with_llm.py --limit 20
.\.venv\Scripts\python.exe scripts\enrich_with_llm.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Les tests ne sollicitent pas le modèle : ils vérifient notamment le nettoyage,
les erreurs, la comparaison, le stockage du booléen nullable et la compatibilité
des checkpoints.
