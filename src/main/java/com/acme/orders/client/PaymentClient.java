package com.acme.orders.client;

import com.acme.orders.domain.*;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.*;

@FeignClient(name = "payment-service", url = "${payments.url}")
public interface PaymentClient {
    @PostMapping("/v2/charges")
    PaymentResult charge(@RequestBody ChargeRequest request);

    @PostMapping("/v2/charges/{paymentId}/refund")
    void refund(@PathVariable("paymentId") String paymentId);
}
