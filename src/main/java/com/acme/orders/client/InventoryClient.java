package com.acme.orders.client;

import com.acme.orders.domain.*;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.*;
import java.util.List;

@FeignClient(name = "inventory-service")
public interface InventoryClient {
    @PostMapping("/reservations")
    InventoryStatus reserve(@RequestBody List<OrderItem> items);

    @DeleteMapping("/reservations")
    void release(@RequestBody List<OrderItem> items);
}
