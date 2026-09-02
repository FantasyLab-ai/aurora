export class DomainError extends Error {
  constructor(
    message: string,
    readonly code: string,
  ) {
    super(message);
    this.name = new.target.name;
  }
}

export class ValidationError extends DomainError {
  constructor(message: string) {
    super(message, 'VALIDATION_ERROR');
  }
}

export class NotFoundError extends DomainError {
  constructor(message: string) {
    super(message, 'NOT_FOUND');
  }
}

export class InvalidStateTransitionError extends DomainError {
  constructor(message: string) {
    super(message, 'INVALID_STATE_TRANSITION');
  }
}

export class InsufficientBalanceError extends DomainError {
  constructor(message: string) {
    super(message, 'INSUFFICIENT_BALANCE');
  }
}

export class DuplicateTransactionError extends DomainError {
  constructor(message: string) {
    super(message, 'DUPLICATE_TRANSACTION');
  }
}
