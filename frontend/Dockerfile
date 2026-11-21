# Frontend Methadata: serwer Vite z proxy /api i /static do backendu.
FROM node:22-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ENV BACKEND_URL=http://backend:8000
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
