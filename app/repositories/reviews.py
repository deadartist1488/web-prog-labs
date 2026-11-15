from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Review


class ReviewRepository:
    def __init__(self, session: Session):
        self.s = session

    def get(self, review_id: int) -> Optional[Review]:
        return self.s.get(Review, review_id)

    def list_for_book(self, book_id: int) -> List[Review]:
        return list(self.s.scalars(select(Review).where(Review.book_id == book_id).order_by(Review.id)))

    def exists(self, user_id: int, book_id: int) -> bool:
        return self.s.scalar(
            select(Review.id).where(Review.user_id == user_id, Review.book_id == book_id)
        ) is not None

    def add(self, review: Review) -> Review:
        self.s.add(review)
        self.s.flush()
        return review

    def delete(self, review: Review) -> None:
        self.s.delete(review)
        self.s.flush()
