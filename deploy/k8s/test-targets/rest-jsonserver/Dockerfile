FROM node:20-alpine

RUN npm install -g json-server@0.17.4

WORKDIR /data

COPY db.json /data/db.json
COPY routes.json /data/routes.json

EXPOSE 3000

CMD ["json-server", "--host", "0.0.0.0", "--port", "3000", "--routes", "routes.json", "/data/db.json"]
