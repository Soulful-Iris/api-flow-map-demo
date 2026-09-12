package com.acme.orders.client;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.*;

@FeignClient(name = "notification-service")
public interface NotificationClient {
    @PostMapping("/emails/order-confirmation")
    void sendConfirmation(@RequestParam("orderId") Long orderId, @RequestParam("email") String email);
}
