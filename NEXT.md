# NEXT — lact-mcp

Priorisé impact/effort. Mettre à jour en fin de session.

1. **About/topics GitHub** (description + topics du repo) — bloqué par les scopes du token (`repo` sans `administration:write`) ; à faire dès qu'un PAT avec ce scope est dispo.
2. **Test hors-ligne** : `python3 lact_mcp.py --test` exige `lactd`; formaliser le smoke test MCP (initialize/tools-list, déjà dans `ci.yml`) en cible `make check` unique.
3. **Chemins AMD** : `voltage` (offset/min/max) n'a été validé qu'en erreur sur un GPU AMD réel (iGPU verrouillé) — à revalider sur une carte RDNA.
4. **`daemon_query`** : documenter/traiter les écritures non confirmées (`reset_pmfw`, `disable_overdrive`, `rest_config`) au-delà des `set_*` couverts par `auto_confirms()` — vérifié : `create/delete_profile` (+ base `provided`) n'ont pas besoin de confirm.
5. **Fan NVIDIA** : `fan set` n'a jamais été appliqué en écriture sur le 3080 (driver propriétaire, hwmon souvent absent) — vérifier au prochain changement de driver.
