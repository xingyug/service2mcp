.PHONY: dev build lint typecheck docker-build

dev:
	npm run dev

build:
	npm run build

lint:
	npm run lint

typecheck:
	npx tsc --noEmit

docker-build:
	docker build -t tool-compiler-web-ui:dev .
