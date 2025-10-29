from __future__ import annotations

import pkgutil

# Rabbit extension needs to come last. All other extensions need to be
# initialized prior to Rabbit for proper registration of Rabbit queues and handlers.
EXTENSIONS = sorted(
    (module.name for module in pkgutil.iter_modules(__path__, f"{__package__}.")),
    key=lambda name: name != "extensions.rabbit",
)
