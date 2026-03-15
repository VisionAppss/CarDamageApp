# frontend/Dockerfile
# Nginx для раздачи статики

FROM nginx:alpine

# Копируем HTML
COPY index.html /usr/share/nginx/html/

# Копируем конфиг nginx
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Порт
EXPOSE 80