"""Общие типы для маршрутов."""
from typing import Annotated

from fastapi import Form, Path

# Идентификатор в адресе: положительное число в пределах 32 бит. Слишком большое число
# или не число дает "страница не найдена", а не ошибку сервера.
IdPath = Annotated[int, Path(ge=1, le=2**31 - 1)]

# То же для чисел из форм: подставленный в форму огромный номер дает ошибку 400 с понятной страницей, а не 500
IdForm = Annotated[int, Form(ge=1, le=2**31 - 1)]
SmallIntForm = Annotated[int, Form(ge=0, le=1000)]
