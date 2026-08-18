PYTHON ?= python3
RUN = PYTHONPATH=src $(PYTHON) -m recipemonster_data --root .
LANGUAGES = en pl de es it
RELEASE_DIR = dist/release
RELEASE_SOURCES = ciqual usda-sr-legacy openfoodfacts-ingredients
RELEASE_ARCHIVES = $(foreach language,$(LANGUAGES),$(RELEASE_DIR)/ingredients_$(language).tar.gz) $(RELEASE_DIR)/nutrition.tar.gz

.PHONY: download download-release draft build build-release validate test archives release-notes release clean

download:
	$(RUN) download

download-release:
	$(RUN) download $(foreach source,$(RELEASE_SOURCES),--source $(source))

draft:
	$(RUN) draft

build:
	$(RUN) build

build-release:
	$(RUN) build $(foreach source,$(RELEASE_SOURCES),--source $(source))

validate:
	$(RUN) validate

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

archives:
	mkdir -p $(RELEASE_DIR)
	rm -f $(RELEASE_ARCHIVES)
	@set -eu; for language in $(LANGUAGES); do \
		tar --sort=name --mtime='UTC 1980-01-01' --owner=0 --group=0 --numeric-owner -C dist -cf "$(RELEASE_DIR)/ingredients_$${language}.tar" "ingredients_$${language}.csv" ATTRIBUTIONS.txt SOURCES.md; \
		gzip -n -9 "$(RELEASE_DIR)/ingredients_$${language}.tar"; \
	done
	tar --sort=name --mtime='UTC 1980-01-01' --owner=0 --group=0 --numeric-owner -C dist -cf $(RELEASE_DIR)/nutrition.tar nutrition.csv ATTRIBUTIONS.txt SOURCES.md
	gzip -n -9 $(RELEASE_DIR)/nutrition.tar

release-notes:
	test -n "$(TAG)"
	$(RUN) release-notes --tag "$(TAG)" --output $(RELEASE_DIR)/RELEASE_NOTES.md

release: download-release
	$(MAKE) test
	$(MAKE) build-release
	$(MAKE) validate
	$(MAKE) archives
	@if test -n "$(TAG)"; then $(MAKE) release-notes TAG="$(TAG)"; fi

clean:
	@echo "Remove raw/ and dist/ contents manually when you intend to discard downloaded or generated data."
