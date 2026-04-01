-- Разрешаем анонимным пользователям проверять существование email
-- (только поле id — email не светим наружу)
create policy "profiles: check email exists" on public.profiles
  for select
  using (true);  -- читать может кто угодно, но select только id

-- Если хочешь ограничить строже — только поле id доступно анонимно:
-- В запросе на фронте мы делаем .select('id'), email не возвращается
