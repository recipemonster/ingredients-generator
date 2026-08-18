PYTHON ?= python3
RUN = PYTHONPATH=src $(PYTHON) -m recipemonster_data --root .
LANGUAGES = en pl de es it
RELEASE_DIR = dist/release
RELEASE_SOURCES = ciqual usda-sr-legacy openfoodfacts-ingredients
RELEASE_ARCHIVES = $(foreach language,$(LANGUAGES),$(RELEASE_DIR)/ingredients_$(language).tar.gz) $(RELEASE_DIR)/nutrition.tar.gz
PREVIOUS_CATALOG_FLAG = $(if $(strip $(PREVIOUS_CATALOG)),--previous-catalog "$(PREVIOUS_CATALOG)",)
PREVIEW_FLAG = $(if $(strip $(CANDIDATE_DIR)),--candidate "$(CANDIDATE_DIR)" --candidate-tag "$(CANDIDATE_TAG)" --pull-request "$(PREVIEW_NUMBER)",)

.PHONY: download download-release refresh-sources draft build build-release validate test archives release-notes pages next-version latest-version validate-version validate-release-tag release clean

download:
	$(RUN) download

download-release:
	$(RUN) download $(foreach source,$(RELEASE_SOURCES),--source $(source))

refresh-sources:
	$(RUN) refresh-sources

draft:
	$(RUN) draft $(PREVIOUS_CATALOG_FLAG)

build:
	$(RUN) build $(PREVIOUS_CATALOG_FLAG)

build-release:
	$(RUN) build $(foreach source,$(RELEASE_SOURCES),--source $(source)) $(PREVIOUS_CATALOG_FLAG)

validate:
	$(RUN) validate

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

archives:
	mkdir -p $(RELEASE_DIR)
	rm -f $(RELEASE_ARCHIVES)
	@set -eu; for language in $(LANGUAGES); do \
		tar --sort=name --mtime='UTC 1980-01-01' --owner=0 --group=0 --numeric-owner -C dist -cf "$(RELEASE_DIR)/ingredients_$${language}.tar" "ingredients_$${language}.csv" ATTRIBUTIONS.md LICENCE.md; \
		gzip -n -9 "$(RELEASE_DIR)/ingredients_$${language}.tar"; \
	done
	tar --sort=name --mtime='UTC 1980-01-01' --owner=0 --group=0 --numeric-owner -C dist -cf $(RELEASE_DIR)/nutrition.tar nutrition.csv ATTRIBUTIONS.md LICENCE.md
	gzip -n -9 $(RELEASE_DIR)/nutrition.tar

release-notes:
	test -n "$(TAG)"
	$(RUN) release-notes --tag "$(TAG)" --output $(RELEASE_DIR)/RELEASE_NOTES.md

pages:
	test -n "$(RELEASES_DIR)"
	$(RUN) pages --releases "$(RELEASES_DIR)" --output dist/pages $(PREVIEW_FLAG)

next-version:
	test -n "$(PUBLISHED_TAGS)"
	$(RUN) next-version --published-tags "$(PUBLISHED_TAGS)" --output VERSION

latest-version:
	test -n "$(PUBLISHED_TAGS)"
	$(RUN) latest-version --published-tags "$(PUBLISHED_TAGS)"

validate-version:
	test -n "$(PUBLISHED_TAGS)"
	$(RUN) validate-version --published-tags "$(PUBLISHED_TAGS)"

validate-release-tag:
	test -n "$(TAG)"
	$(RUN) validate-release-tag --tag "$(TAG)"

release: download-release
	$(MAKE) test
	$(MAKE) build-release PREVIOUS_CATALOG="$(PREVIOUS_CATALOG)"
	$(MAKE) validate
	$(MAKE) archives
	@if test -n "$(TAG)"; then $(MAKE) release-notes TAG="$(TAG)"; fi

clean:
	@echo "Remove raw/ and dist/ contents manually when you intend to discard downloaded or generated data."
