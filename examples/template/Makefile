
set-image-name:
	variable="my-image-$$(date --iso)"; jq --arg variable "$$variable" '.name = $$variable' template.json > image.json


build-image: set-image-name
	wolkenbrot bake image.json
