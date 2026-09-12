package com.acme.orders.domain;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.ResponseStatus;
import java.util.List;

@ResponseStatus(HttpStatus.CONFLICT)
public class OutOfStockException extends RuntimeException {
    public OutOfStockException(List<String> skus) { super("out of stock: " + skus); }
}
