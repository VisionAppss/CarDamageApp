# frontend/Dockerfile
# Nginx для раздачи статики

FROM nginx:alpine

# Копируем HTML
COPY index.html /usr/share/nginx/html/
RUN chmod 644 /usr/share/nginx/html/index.html
COPY auth.html /usr/share/nginx/html/
RUN chmod 644 /usr/share/nginx/html/auth.html
COPY supabase.js /usr/share/nginx/html/
RUN chmod 644 /usr/share/nginx/html/supabase.js

# Копируем конфиг nginx
COPY icons/ /usr/share/nginx/html/icons/
COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY robots.txt /usr/share/nginx/html/
COPY sitemap.xml /usr/share/nginx/html/
# Порт
EXPOSE 80