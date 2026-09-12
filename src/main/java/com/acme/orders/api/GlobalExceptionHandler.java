package com.acme.orders.api;

import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import com.acme.orders.domain.OrderNotFoundException;
import com.acme.orders.domain.PaymentDeclinedException;

@RestControllerAdvice
public class GlobalExceptionHandler {
    @ExceptionHandler(OrderNotFoundException.class)
    @ResponseStatus(HttpStatus.NOT_FOUND)
    public ErrorBody notFound(OrderNotFoundException ex) { return new ErrorBody(ex.getMessage()); }

    @ExceptionHandler(PaymentDeclinedException.class)
    public ResponseEntity<ErrorBody> declined(PaymentDeclinedException ex) {
        return ResponseEntity.status(HttpStatus.PAYMENT_REQUIRED).body(new ErrorBody(ex.getMessage()));
    }
}
