FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json /app/
RUN npm ci --omit=dev

COPY server.js /app/server.js

EXPOSE 3000

CMD ["npm", "start"]
